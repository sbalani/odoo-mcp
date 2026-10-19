from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged
from odoo.tests.common import new_test_user


@tagged('post_install', '-at_install')
class TestMcpPurchase(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(cls.env, login='mcp_buyer', groups='base.group_user,purchase.group_purchase_manager,stock.group_stock_user,account.group_account_invoice,odoo_mcp.group_mcp_purchase_write,odoo_mcp.group_mcp_product_write')
        cls.gateway = cls.env['odoo.mcp.gateway'].with_user(cls.user).with_context(allowed_company_ids=cls.env.company.ids)
        cls.vendor = cls.env['res.partner'].create({'name': 'MCP supplier'})
        cls.unit = cls.env.ref('uom.product_uom_unit')
        cls.product = cls.env['product.product'].create({'name': 'MCP purchased product', 'purchase_ok': True,
            'uom_id': cls.unit.id, 'uom_po_id': cls.unit.id, 'supplier_taxes_id': [Command.clear()]})
        cls.journal = cls.env['account.journal'].create({'name': 'MCP purchase journal', 'code': 'MCPP', 'type': 'purchase'})
        cls.expense = cls.env['account.account'].create({'name': 'MCP expense', 'code': 'MCP600', 'account_type': 'expense'})
        cls.tax = cls.env['account.tax'].create({'name': 'MCP purchase VAT', 'amount': 10, 'type_tax_use': 'purchase'})

    def bill(self, **updates):
        return self.env['account.move'].create({'move_type': 'in_invoice', 'journal_id': self.journal.id,
            'partner_id': self.vendor.id, 'ref': 'SUP-001', 'invoice_date': '2026-10-01',
            'invoice_line_ids': [Command.create({'product_id': self.product.id, 'product_uom_id': self.unit.id,
                'quantity': 3, 'price_unit': 17.25, 'discount': 5, 'name': 'Exact supplier description',
                'account_id': self.expense.id, 'tax_ids': [Command.set(self.tax.ids)]})], **updates})

    def manual_args(self):
        return {'request_key': 'supplier-invoice-001', 'partner_id': self.vendor.id,
            'partner_ref': 'INV-001', 'currency_id': self.env.company.currency_id.id,
            'lines': [{'product_id': self.product.id, 'quantity': 2, 'price_unit': 11.2, 'tax_ids': []}]}

    def test_bill_to_draft_preserves_invoice_and_retry(self):
        bill = self.bill()
        before = bill.read(['state', 'amount_total', 'write_date', 'invoice_line_ids'])[0]
        result = self.gateway._call('purchase_create_from_bill', {'bill_id': bill.id})
        order = self.env['purchase.order'].browse(result['id'])
        self.assertTrue(result['total_matches_bill'])
        self.assertEqual(order.state, 'draft')
        self.assertEqual(order.mcp_source_bill_id, bill)
        self.assertEqual(order.company_id, bill.company_id)
        self.assertEqual(order.currency_id, bill.currency_id)
        self.assertEqual(order.partner_ref, bill.ref)
        self.assertEqual(order.order_line.product_qty, 3)
        self.assertEqual(order.order_line.price_unit, 17.25)
        self.assertEqual(order.order_line.discount, 5)
        self.assertEqual(order.order_line.name, 'Exact supplier description')
        self.assertEqual(order.order_line.taxes_id, self.tax)
        self.assertAlmostEqual(order.amount_total, bill.amount_total, places=2)
        self.assertFalse(order.picking_ids)
        self.assertFalse(bill.invoice_line_ids.purchase_line_id)
        self.assertEqual(before, bill.read(['state', 'amount_total', 'write_date', 'invoice_line_ids'])[0])
        retried = self.gateway._call('purchase_create_from_bill', {'bill_id': bill.id})
        self.assertEqual(retried['id'], order.id)
        self.assertTrue(retried['reused'])

    def test_manual_purchase_retry_and_conflicting_key(self):
        args = self.manual_args()
        result = self.gateway._call('purchase_create', args)
        self.assertEqual(result['state'], 'draft')
        self.assertEqual(self.gateway._call('purchase_create', args)['id'], result['id'])
        with self.assertRaises(ValueError):
            self.gateway._call('purchase_create', {**args, 'partner_ref': 'DIFFERENT'})

    def test_product_creation_tracking_units_and_duplicate(self):
        values = {'name': 'MCP lot product', 'default_code': 'MCP-LOT-01', 'uom_id': self.unit.id}
        result = self.gateway._call('product_create', {'values': values})
        product = self.env['product.product'].browse(result['id'])
        self.assertEqual(product.tracking, 'lot')
        self.assertTrue(product.is_storable)
        self.assertTrue(product.purchase_ok)
        self.assertEqual(product.company_id, self.env.company)
        self.assertEqual(product.uom_po_id, self.unit)
        self.assertFalse(self.env['stock.lot'].search([('product_id', '=', product.id)]))
        with self.assertRaises(ValueError):
            self.gateway._call('product_create', {'values': values})
        service = self.gateway._call('product_create', {'values': {**values, 'default_code': 'MCP-SERVICE', 'type': 'service'}})
        self.assertEqual(service['tracking'], 'none')
        self.assertFalse(service['is_storable'])
        serial = self.gateway._call('product_create', {'values': {**values, 'default_code': 'MCP-SERIAL', 'tracking': 'serial'}})
        self.assertEqual(serial['tracking'], 'serial')

    def test_no_automatic_new_rights_for_existing_oauth(self):
        old = self.gateway.with_context(mcp_oauth_scopes=['mcp.read', 'mcp.crm.write'])
        for name in ('purchase_create', 'purchase_create_from_bill', 'product_create'):
            with self.assertRaises(AccessError):
                old._call(name, {})
        purchase = self.gateway.with_context(mcp_oauth_scopes=['mcp.read', 'mcp.purchase.write'])
        self.assertIn('purchase_create', [t['name'] for t in purchase._tools()])
        self.assertNotIn('product_create', [t['name'] for t in purchase._tools()])
        for name in ('purchase_confirm', 'purchase_receive', 'invoice_update', 'product_update', 'lot_create'):
            with self.assertRaises(AccessError):
                self.gateway._call(name, {})

    def test_native_acl_still_required(self):
        reader = new_test_user(self.env, login='mcp_no_purchase_acl', groups='base.group_user,odoo_mcp.group_mcp_purchase_write')
        with self.assertRaises(AccessError):
            self.gateway.with_user(reader)._call('purchase_create', self.manual_args())

    def test_company_boundaries(self):
        other = self.env['res.company'].create({'name': 'MCP other buyer'})
        self.user.company_ids = [Command.link(other.id)]
        partner = self.env['res.partner'].create({'name': 'Other company supplier', 'company_id': other.id})
        with self.assertRaises(AccessError):
            self.gateway._call('purchase_create', {**self.manual_args(), 'partner_id': partner.id})
        product = self.env['product.product'].create({'name': 'Other product', 'company_id': other.id})
        args = self.manual_args()
        args['lines'][0]['product_id'] = product.id
        with self.assertRaises(AccessError):
            self.gateway._call('purchase_create', args)

    def test_bill_mapping_requires_explicit_valid_product(self):
        bill = self.bill()
        line = bill.invoice_line_ids
        line.product_id = False
        with self.assertRaises(ValueError):
            self.gateway._call('purchase_create_from_bill', {'bill_id': bill.id})
        result = self.gateway._call('purchase_create_from_bill', {'bill_id': bill.id,
            'line_products': [{'line_id': line.id, 'product_id': self.product.id, 'uom_id': self.unit.id}]})
        self.assertEqual(self.env['purchase.order'].browse(result['id']).order_line.product_id, self.product)
        self.assertFalse(line.product_id)

    def test_reject_existing_purchase_link_credit_note_and_wrong_unit(self):
        bill = self.bill()
        result = self.gateway._call('purchase_create', self.manual_args())
        bill.invoice_line_ids.purchase_line_id = self.env['purchase.order'].browse(result['id']).order_line
        with self.assertRaises(ValueError):
            self.gateway._call('purchase_create_from_bill', {'bill_id': bill.id})
        credit = self.bill(move_type='in_refund')
        with self.assertRaises(ValueError):
            self.gateway._call('purchase_create_from_bill', {'bill_id': credit.id})
        args = self.manual_args()
        args['request_key'] = 'bad-unit'
        args['lines'][0]['uom_id'] = self.env.ref('uom.product_uom_kgm').id
        with self.assertRaises(ValueError):
            self.gateway._call('purchase_create', args)

    def test_supplier_price_does_not_override_invoice_currency_unit_or_tax(self):
        currency = self.env.ref('base.USD')
        currency.active = True
        dozen = self.env.ref('uom.product_uom_dozen')
        self.env['product.supplierinfo'].create({'partner_id': self.vendor.id,
            'product_tmpl_id': self.product.product_tmpl_id.id, 'price': 999})
        # Included tax amounts must remain as stated on the bill, not be added again.
        self.tax.price_include_override = 'tax_included'
        bill = self.bill(currency_id=currency.id)
        bill.invoice_line_ids.write({'product_uom_id': dozen.id, 'price_unit': 132, 'discount': 10})
        result = self.gateway._call('purchase_create_from_bill', {'bill_id': bill.id})
        order = self.env['purchase.order'].browse(result['id'])
        self.assertEqual(order.currency_id, currency)
        self.assertEqual(order.order_line.product_uom, dozen)
        self.assertEqual(order.order_line.price_unit, 132)
        self.assertEqual(order.order_line.discount, 10)
        self.assertAlmostEqual(order.amount_total, bill.amount_total, places=2)

    def test_product_creation_requires_native_product_acl(self):
        user = new_test_user(self.env, login='mcp_product_readonly',
            groups='base.group_user,purchase.group_purchase_user,odoo_mcp.group_mcp_product_write')
        with self.assertRaises(AccessError):
            self.gateway.with_user(user)._call('product_create', {'values': {
                'name': 'Denied', 'default_code': 'MCP-DENIED', 'uom_id': self.unit.id}})
