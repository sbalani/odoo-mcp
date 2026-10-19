"""Draft purchase creation; invoices and stock movements remain read-only."""
import hashlib
import json

from odoo import Command, fields, models
from odoo.exceptions import AccessError

from ..policy import (PURCHASE_LINE_SCHEMA, finite_number, integer, object_values,
                      product_values, text_value)


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    mcp_source_bill_id = fields.Many2one('account.move', copy=False, readonly=True,
        check_company=True, index=True, ondelete='restrict', string='MCP source vendor bill')
    mcp_request_key = fields.Char(copy=False, readonly=True, index=True)
    mcp_request_hash = fields.Char(copy=False, readonly=True)
    _sql_constraints = [
        ('mcp_source_bill_unique', 'unique(mcp_source_bill_id)', 'An MCP purchase order already exists for this bill.'),
        ('mcp_request_unique', 'unique(company_id, mcp_request_key)', 'This MCP purchase request already exists.'),
    ]


class PurchaseGateway(models.AbstractModel):
    _inherit = 'odoo.mcp.gateway'

    def _purchase_related(self, model, record_id):
        record = self._record(model, record_id)
        if 'company_id' in record._fields and record.company_id and record.company_id != self.env.company:
            raise AccessError('Related record belongs to another company.')
        if 'active' in record._fields and not record.active:
            raise ValueError('Archived records cannot be used for purchasing')
        return record

    def _purchase_result(self, order, reused=False):
        order.check_access('read')
        return {'id': order.id, 'name': order.name, 'state': order.state,
                'company_id': order.company_id.id, 'currency_id': order.currency_id.id,
                'amount_total': order.amount_total, 'reused': reused,
                'source_bill_id': order.mcp_source_bill_id.id or False}

    def _purchase_line(self, data):
        values = object_values(data, PURCHASE_LINE_SCHEMA['properties'], PURCHASE_LINE_SCHEMA['required'])
        product = self._purchase_related('product.product', values['product_id'])
        if not product.purchase_ok:
            raise ValueError('Product is not enabled for purchasing')
        quantity = finite_number(values['quantity'], 'quantity')
        if quantity <= 0:
            raise ValueError('Purchase quantities must be positive')
        uom = self._purchase_related('uom.uom', values.get('uom_id', product.uom_po_id.id))
        if uom.category_id != product.uom_id.category_id:
            raise ValueError('Purchase unit must be in the product unit category')
        taxes = values['tax_ids']
        if not isinstance(taxes, list) or len(taxes) > 20:
            raise ValueError('tax_ids must contain at most 20 IDs; use [] for no tax')
        for tax_id in taxes:
            tax = self._purchase_related('account.tax', tax_id)
            if tax.type_tax_use != 'purchase':
                raise ValueError('Only purchase taxes are allowed')
        return {'product_id': product.id, 'product_qty': quantity,
                'product_uom': uom.id,
                'price_unit': finite_number(values['price_unit'], 'price_unit'),
                'discount': finite_number(values.get('discount', 0), 'discount', 0, 100),
                'name': text_value(values.get('name', product.display_name), 'name'),
                'taxes_id': [Command.set(taxes)], 'date_planned': fields.Datetime.now()}

    def _create_purchase(self, partner_id, currency_id, ref, lines, **extra):
        self._purchase_related('res.partner', partner_id)
        self._purchase_related('res.currency', currency_id)
        if not isinstance(lines, list) or not 1 <= len(lines) <= 100:
            raise ValueError('Provide between 1 and 100 purchase lines')
        prepared = [self._purchase_line(line) for line in lines]
        order = self.env['purchase.order'].create({
            'partner_id': partner_id, 'currency_id': currency_id,
            'company_id': self.env.company.id,
            'partner_ref': text_value(ref, 'partner_ref', 256),
            'order_line': [Command.create(line) for line in prepared], **extra})
        # Odoo computes supplier prices/descriptions on product and quantity changes.
        # Reapply explicit invoice values after those computes have run.
        order.flush_recordset()
        for line, vals in zip(order.order_line.sorted('id'), prepared):
            line.write({key: vals[key] for key in ('name', 'price_unit', 'discount', 'taxes_id')})
        if order.state != 'draft':
            raise ValueError('Purchase creation must result in a draft RFQ')
        return self._purchase_result(order)

    def _purchase_from_bill(self, args):
        bill = self._purchase_related('account.move', args['bill_id'])
        if bill.company_id != self.env.company or bill.move_type != 'in_invoice' or bill.state not in ('draft', 'posted'):
            raise ValueError('Select a draft or posted vendor bill in this company')
        # Serialize retries without modifying accounting records or bypassing ACLs.
        self.env.cr.execute('SELECT id FROM account_move WHERE id = %s FOR UPDATE', [bill.id])
        existing = self.env['purchase.order'].search([('mcp_source_bill_id', '=', bill.id)], limit=1)
        if existing:
            return self._purchase_result(existing, True)
        bill.invalidate_recordset()
        bill_lines = bill.invoice_line_ids.filtered(lambda line: line.display_type == 'product')
        if any(bill_lines.mapped('purchase_line_id')):
            raise ValueError('This bill already has purchase-linked lines; review its existing POs')
        mappings = args.get('line_products', [])
        if not isinstance(mappings, list) or len(mappings) > 100:
            raise ValueError('line_products must contain at most 100 mappings')
        products = {}
        for mapping in mappings:
            item = object_values(mapping, ('line_id', 'product_id', 'uom_id'), ('line_id', 'product_id'))
            line_id = integer(item['line_id'], 'line_id')
            if line_id not in bill_lines.ids or line_id in products:
                raise ValueError('Product mappings must refer to distinct lines of this bill')
            integer(item['product_id'], 'product_id')
            if 'uom_id' in item:
                integer(item['uom_id'], 'uom_id')
            products[line_id] = item
        lines = []
        for line in bill_lines:
            mapping = products.get(line.id, {})
            product_id = mapping.get('product_id', line.product_id.id)
            if not product_id:
                raise ValueError('Look up or create a product and supply line_products for bill line %s' % line.id)
            if line.product_id and product_id != line.product_id.id:
                raise ValueError('Cannot override the product already recorded on a vendor bill')
            uom_id = mapping.get('uom_id', line.product_uom_id.id)
            if not uom_id:
                raise ValueError('Bill line %s has no unit; supply an explicit uom_id in line_products' % line.id)
            if line.product_uom_id and uom_id != line.product_uom_id.id:
                raise ValueError('Cannot override the unit already recorded on a vendor bill')
            lines.append({'product_id': product_id, 'quantity': line.quantity,
                          'uom_id': uom_id, 'price_unit': line.price_unit,
                          'discount': line.discount, 'tax_ids': line.tax_ids.ids, 'name': line.name})
        if bill.fiscal_position_id:
            self._purchase_related('account.fiscal.position', bill.fiscal_position_id.id)
        result = self._create_purchase(bill.partner_id.id, bill.currency_id.id, bill.ref or bill.name,
            lines, mcp_source_bill_id=bill.id, origin=bill.name,
            fiscal_position_id=bill.fiscal_position_id.id or False)
        result['source_bill_total'] = bill.amount_total
        result['total_matches_bill'] = bill.currency_id.compare_amounts(result['amount_total'], bill.amount_total) == 0
        return result

    def _execute(self, name, args):
        if name == 'purchase_create_from_bill':
            return self._purchase_from_bill(args)
        if name == 'purchase_create':
            key = text_value(args['request_key'], 'request_key', 128)
            fingerprint = hashlib.sha256(json.dumps(args, sort_keys=True, allow_nan=False).encode()).hexdigest()
            self.env.cr.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',
                                ['mcp_purchase:%s:%s' % (self.env.company.id, key)])
            existing = self.env['purchase.order'].search([
                ('company_id', '=', self.env.company.id), ('mcp_request_key', '=', key)], limit=1)
            if existing:
                if existing.mcp_request_hash != fingerprint:
                    raise ValueError('request_key was already used with different purchase details')
                return self._purchase_result(existing, True)
            return self._create_purchase(args['partner_id'], args['currency_id'], args['partner_ref'],
                args['lines'], mcp_request_key=key, mcp_request_hash=fingerprint)
        if name == 'product_create':
            values = product_values(args['values'])
            unit = self._purchase_related('uom.uom', values['uom_id'])
            purchase_unit = self._purchase_related('uom.uom', values.get('uom_po_id', unit.id))
            if unit.category_id != purchase_unit.category_id:
                raise ValueError('Stock and purchase units must use the same category')
            if values.get('categ_id'):
                self._purchase_related('product.category', values['categ_id'])
            self.env.cr.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',
                                ['mcp_product:' + values['default_code']])
            products = self.env['product.product'].with_context(active_test=False)
            duplicates = products.search([('default_code', '=', values['default_code'])], limit=5)
            if duplicates:
                raise ValueError('Product reference already exists; inspect product IDs %s' % duplicates.ids)
            if values.get('barcode') and products.search_count([('barcode', '=', values['barcode'])]):
                raise ValueError('Product barcode already exists')
            values.update({'company_id': self.env.company.id, 'purchase_ok': True,
                           'is_storable': values['type'] == 'consu', 'uom_po_id': purchase_unit.id})
            values.setdefault('sale_ok', False)
            product = self.env['product.product'].create(values)
            return {'id': product.id, 'product_tmpl_id': product.product_tmpl_id.id,
                    'company_id': product.company_id.id, 'tracking': product.tracking,
                    'is_storable': product.is_storable, 'purchase_ok': product.purchase_ok}
        return super()._execute(name, args)
