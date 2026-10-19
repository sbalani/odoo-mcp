"""Explicit public API. No caller-controlled model, method, context or field paths."""
RESOURCES = {
    'sales': ('sale.order', 'name partner_id date_order state amount_untaxed amount_tax amount_total currency_id company_id user_id order_line'),
    'sale_lines': ('sale.order.line', 'order_id product_id name product_uom_qty qty_delivered qty_invoiced price_unit price_subtotal product_uom company_id'),
    'products': ('product.product', 'name display_name default_code barcode type is_storable uom_id uom_po_id tracking active purchase_ok sale_ok categ_id company_id seller_ids'),
    'purchase_orders': ('purchase.order', 'name partner_id partner_ref state date_order date_planned currency_id company_id order_line amount_untaxed amount_tax amount_total origin mcp_source_bill_id mcp_request_key'),
    'purchase_lines': ('purchase.order.line', 'order_id product_id name product_qty product_uom price_unit discount taxes_id date_planned price_subtotal price_total qty_received qty_invoiced company_id'),
    'supplier_products': ('product.supplierinfo', 'partner_id product_tmpl_id product_id product_name product_code min_qty price currency_id product_uom company_id date_start date_end delay'),
    'units': ('uom.uom', 'name category_id uom_type factor rounding active'),
    'product_categories': ('product.category', 'name complete_name parent_id'),
    'purchase_taxes': ('account.tax', 'name type_tax_use amount_type amount price_include active company_id'),
    'currencies': ('res.currency', 'name symbol rounding active'),
    'inventory': ('stock.quant', 'product_id location_id lot_id quantity reserved_quantity available_quantity company_id'),
    'lots': ('stock.lot', 'name product_id product_qty company_id create_date'),
    'warehouses': ('stock.warehouse', 'name code company_id lot_stock_id'),
    'locations': ('stock.location', 'name complete_name usage location_id company_id'),
    'transfers': ('stock.picking', 'name origin state partner_id scheduled_date date_done location_id location_dest_id company_id'),
    'stock_moves': ('stock.move.line', 'picking_id product_id lot_id quantity product_uom_id location_id location_dest_id state date company_id'),
    'invoices': ('account.move', 'name partner_id move_type state invoice_date invoice_date_due amount_untaxed amount_tax amount_total amount_residual payment_state currency_id company_id invoice_line_ids ref invoice_origin create_date write_date'),
    'invoice_lines': ('account.move.line', 'move_id product_id name quantity price_unit price_subtotal price_total currency_id company_id product_uom_id discount tax_ids purchase_line_id'),
    'crm': ('crm.lead', 'name type active partner_id contact_name email_from phone user_id team_id stage_id priority probability expected_revenue date_deadline description company_id'),
    'crm_stages': ('crm.stage', 'name sequence is_won team_id'),
    'crm_teams': ('crm.team', 'name company_id user_id'),
    'crm_activities': ('mail.activity', 'res_id activity_type_id summary note date_deadline user_id'),
    'activity_types': ('mail.activity.type', 'name category res_model'),
    'contacts': ('res.partner', 'name email phone mobile is_company parent_id active company_id'),
    'users': ('res.users', 'name partner_id active'),
    'channels': ('discuss.channel', 'name channel_type is_member'),
    'todos': ('project.task', 'name description state priority date_deadline user_ids personal_stage_type_id create_date write_date'),
}
RESOURCES = {key: (model, fields.split()) for key, (model, fields) in RESOURCES.items()}
CRM_FIELDS = {
    'name': 'text', 'type': 'text', 'partner_id': 'id', 'contact_name': 'text',
    'email_from': 'text', 'phone': 'text', 'user_id': 'id', 'team_id': 'id',
    'stage_id': 'id', 'priority': 'text', 'probability': 'number',
    'expected_revenue': 'number', 'date_deadline': 'text', 'description': 'text',
}
CHAT_TARGETS = {
    'channel': 'discuss.channel', 'crm': 'crm.lead', 'sale': 'sale.order',
    'invoice': 'account.move', 'lot': 'stock.lot', 'transfer': 'stock.picking',
    'todo': 'project.task',
}


def todo_values(values, creating=False):
    from datetime import datetime
    if not isinstance(values, dict) or not values:
        raise ValueError('values must be a nonempty object')
    if set(values) - {'name', 'description', 'priority', 'date_deadline'}:
        raise ValueError('Only name, description, priority and date_deadline may be written')
    if creating or 'name' in values:
        text_value(values.get('name'), 'name', 256)
    if 'description' in values and (not isinstance(values['description'], str) or len(values['description']) > 10000):
        raise ValueError('description must be text up to 10000 characters')
    if 'priority' in values and values['priority'] not in ('0', '1'):
        raise ValueError('priority must be 0 or 1')
    if 'date_deadline' in values and values['date_deadline'] is not False:
        deadline = text_value(values['date_deadline'], 'date_deadline', 19)
        if datetime.strptime(deadline, '%Y-%m-%d %H:%M:%S').strftime('%Y-%m-%d %H:%M:%S') != deadline:
            raise ValueError('date_deadline must use YYYY-MM-DD HH:MM:SS in UTC, or false to clear')
    return dict(values)


VERSIONS = ('2025-03-26', '2025-06-18', '2025-11-25')
MAX_BODY = 131072


def integer(value, name, minimum=1, maximum=2147483647):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError('%s must be an integer between %s and %s' % (name, minimum, maximum))
    return value


def text_value(value, name, maximum=10000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError('%s must be nonempty text (maximum %s characters)' % (name, maximum))
    return value


def search_args(args):
    resource = args.get('resource')
    if not isinstance(resource, str) or resource not in RESOURCES:
        raise ValueError('Unknown resource')
    model, allowed = RESOURCES[resource]
    fields = args.get('fields', allowed)
    if not isinstance(fields, list) or not fields or any(not isinstance(f, str) or f not in allowed + ['id'] for f in fields):
        raise ValueError('Fields must be selected from the resource allowlist')
    domain = args.get('filters', [])
    if not isinstance(domain, list) or len(domain) > 20:
        raise ValueError('filters must contain at most 20 AND conditions')
    for term in domain:
        if not isinstance(term, (list, tuple)) or len(term) != 3:
            raise ValueError('Each filter must be [field, operator, value]')
        field, operator, value = term
        if not isinstance(field, str) or field not in allowed + ['id']:
            raise ValueError('Filter field is not allowed')
        if not isinstance(operator, str) or operator not in ('=', '!=', '>', '>=', '<', '<=', 'in', 'not in', 'ilike', 'not ilike'):
            raise ValueError('Filter operator is not allowed')
        if operator in ('in', 'not in'):
            if not isinstance(value, list) or len(value) > 100 or any(type(v) not in (str, int, float, bool) for v in value):
                raise ValueError('Membership filters require at most 100 scalar values')
        elif type(value) not in (str, int, float, bool):
            raise ValueError('Filter value must be scalar')
    limit = integer(args.get('limit', 50), 'limit', 1, 100)
    offset = integer(args.get('offset', 0), 'offset', 0, 10000)
    return resource, model, fields, domain, limit, offset


def crm_values(values, creating=False):
    import math
    if not isinstance(values, dict) or not values:
        raise ValueError('values must be a nonempty object')
    if any(key not in CRM_FIELDS for key in values):
        raise ValueError('Only documented CRM fields may be written')
    if creating and not values.get('name'):
        raise ValueError('name is required')
    for key, value in values.items():
        kind = CRM_FIELDS[key]
        if value is False and key != 'name':
            continue
        if kind == 'id':
            integer(value, key)
        elif kind == 'number':
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError('%s must be a finite number' % key)
        elif not isinstance(value, str) or len(value) > 10000:
            raise ValueError('%s must be text up to 10000 characters' % key)
    if 'name' in values:
        text_value(values['name'], 'name', 256)
    if 'probability' in values and not 0 <= values['probability'] <= 100:
        raise ValueError('probability must be between 0 and 100')
    if 'type' in values and values['type'] not in ('lead', 'opportunity'):
        raise ValueError('type must be lead or opportunity')
    if 'priority' in values and values['priority'] not in ('0', '1', '2', '3'):
        raise ValueError('priority must be 0, 1, 2 or 3')
    return dict(values)


def tool(name, description, properties, required=(), write=False):
    return {'name': name, 'description': description,
            'inputSchema': {'type': 'object', 'properties': properties,
                            'required': list(required), 'additionalProperties': False},
            'annotations': {'readOnlyHint': not write, 'destructiveHint': write,
                            'idempotentHint': not write, 'openWorldHint': False}}


ID = {'type': 'integer', 'minimum': 1}
STRING = {'type': 'string', 'minLength': 1, 'maxLength': 10000}
CRM_SCHEMA = {'type': 'object', 'additionalProperties': False, 'properties': {
    key: {'anyOf': [{'type': {'text': 'string', 'id': 'integer', 'number': 'number'}[kind]}, {'const': False}]}
    for key, kind in CRM_FIELDS.items()
}}
READ_TOOLS = [
    tool('odoo_search', 'Read scoped records. Filters are ANDed; use IDs to follow relations. Results ordered by id; page using offset. Use odoo_catalog for fields.', {
        'resource': {'type': 'string', 'enum': list(RESOURCES)},
        'fields': {'type': 'array', 'items': {'type': 'string'}},
        'filters': {'type': 'array', 'items': {'type': 'array', 'minItems': 3, 'maxItems': 3}},
        'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100},
        'offset': {'type': 'integer', 'minimum': 0, 'maximum': 10000},
    }, ['resource']),
    tool('odoo_catalog', 'List exposed models, readable fields and effective write capabilities.', {}),
    tool('odoo_messages', 'Read messages in a joined Discuss channel or an allowed record thread: CRM, sale, invoice, lot, transfer, or your own To-do.', {
        'target': {'type': 'string', 'enum': list(CHAT_TARGETS)}, 'id': ID,
        'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100},
        'before_id': ID,
    }, ['target', 'id']),
]
CRM_TOOLS = [
    tool('crm_create', 'Create a CRM lead/opportunity. No sales documents or contact records are created.', {'values': CRM_SCHEMA}, ['values'], True),
    tool('crm_update', 'Update allowed CRM fields on an existing lead/opportunity.', {'id': ID, 'values': CRM_SCHEMA}, ['id', 'values'], True),
    tool('crm_action', 'Mark a CRM record won/lost, archive or restore it.', {'id': ID, 'action': {'type': 'string', 'enum': ['won', 'lost', 'archive', 'restore']}}, ['id', 'action'], True),
    tool('crm_schedule_activity', 'Schedule an activity on a CRM record. Only ordinary todo-category activity types are supported.', {'id': ID, 'activity_type_id': ID, 'user_id': ID, 'summary': STRING, 'note': STRING, 'date_deadline': {'type': 'string', 'format': 'date'}}, ['id', 'activity_type_id', 'summary', 'date_deadline'], True),
    tool('crm_complete_activity', 'Complete one CRM activity and record feedback.', {'id': ID, 'feedback': STRING}, ['id'], True),
]
CHAT_TOOLS = [tool('chat_post', 'Send plain text to a joined Discuss channel or add an internal note to allowed record chatter. Business fields are unchanged. Odoo posting permissions apply; may notify members/followers.', {
    'target': {'type': 'string', 'enum': list(CHAT_TARGETS)}, 'id': ID, 'body': STRING,
}, ['target', 'id', 'body'], True)]


TODO_SCHEMA = {'type': 'object', 'additionalProperties': False, 'properties': {
    'name': {'type': 'string', 'minLength': 1, 'maxLength': 256},
    'description': {'type': 'string', 'maxLength': 10000},
    'priority': {'type': 'string', 'enum': ['0', '1']},
    'date_deadline': {'anyOf': [{'type': 'string', 'description': 'UTC YYYY-MM-DD HH:MM:SS'}, {'const': False}]},
}}
TODO_TOOLS = [
    tool('todo_create', 'Create a personal To-do assigned to yourself, with no project.', {'values': TODO_SCHEMA}, ['values'], True),
    tool('todo_update', 'Edit your assigned personal To-do. Cannot reassign it or convert it into a project task.', {'id': ID, 'values': TODO_SCHEMA}, ['id', 'values'], True),
    tool('todo_action', 'Complete, reopen or cancel your assigned personal To-do.', {'id': ID, 'action': {'type': 'string', 'enum': ['complete', 'reopen', 'cancel']}}, ['id', 'action'], True),
]


def validate_arguments(definition, args):
    if not isinstance(args, dict):
        raise ValueError('arguments must be an object')
    schema = definition['inputSchema']
    if set(args) - set(schema['properties']) or set(schema['required']) - set(args):
        raise ValueError('Unexpected or missing tool arguments')


PRODUCT_SCHEMA = {'type': 'object', 'additionalProperties': False, 'properties': {
    'name': STRING, 'default_code': STRING, 'barcode': STRING,
    'type': {'type': 'string', 'enum': ['consu', 'service']},
    'tracking': {'type': 'string', 'enum': ['lot', 'serial', 'none']},
    'uom_id': ID, 'uom_po_id': ID, 'categ_id': ID,
    'sale_ok': {'type': 'boolean'},
}, 'required': ['name', 'default_code', 'uom_id']}
PURCHASE_LINE_SCHEMA = {'type': 'object', 'additionalProperties': False, 'properties': {
    'product_id': ID, 'quantity': {'type': 'number', 'exclusiveMinimum': 0},
    'price_unit': {'type': 'number', 'minimum': 0}, 'uom_id': ID, 'name': STRING,
    'discount': {'type': 'number', 'minimum': 0, 'maximum': 100},
    'tax_ids': {'type': 'array', 'items': ID, 'maxItems': 20},
}, 'required': ['product_id', 'quantity', 'price_unit', 'tax_ids']}
PURCHASE_TOOLS = [
    tool('purchase_create_from_bill', 'Create one draft RFQ from an existing vendor bill in this company. Copies quantities, units, prices, discounts, currency and taxes without changing or linking accounting lines. Rejects bills already linked to POs; retries return the existing MCP PO. For missing bill products, supply explicit line/product mappings after lookup, including uom_id if the bill has no unit. Does not confirm, receive, pay or send anything.', {
        'bill_id': ID,
        'line_products': {'type': 'array', 'maxItems': 100, 'items': {
            'type': 'object', 'additionalProperties': False,
            'properties': {'line_id': ID, 'product_id': ID, 'uom_id': ID}, 'required': ['line_id', 'product_id']}},
    }, ['bill_id'], True),
    tool('purchase_create', 'Create a draft RFQ from explicitly supplied supplier invoice details. Use purchase_create_from_bill instead when the bill exists in Odoo. Reuse request_key for retries; never invent products, units or taxes. Does not confirm or receive stock.', {
        'request_key': STRING, 'partner_id': ID, 'currency_id': ID, 'partner_ref': STRING,
        'lines': {'type': 'array', 'minItems': 1, 'maxItems': 100, 'items': PURCHASE_LINE_SCHEMA},
    }, ['request_key', 'partner_id', 'currency_id', 'partner_ref', 'lines'], True),
]
PRODUCT_TOOLS = [tool('product_create', 'Create a company-specific purchasable product and its single variant. Search products and supplier_products first. Physical goods default to inventory tracking by lot; services must use tracking none. Returns the variant ID to use on purchase lines. Does not create lot numbers, quantities or receipts. Existing products cannot be edited.', {'values': PRODUCT_SCHEMA}, ['values'], True)]


def object_values(value, allowed, required):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise ValueError('Unexpected or missing fields')
    return dict(value)


def finite_number(value, name, minimum=0, maximum=1e12):
    import math
    if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError('%s must be a finite number between %s and %s' % (name, minimum, maximum))
    return value


def product_values(value):
    values = object_values(value, PRODUCT_SCHEMA['properties'], PRODUCT_SCHEMA['required'])
    for name in ('name', 'default_code', 'barcode'):
        if name in values:
            text_value(values[name], name, 256)
    for name in ('uom_id', 'uom_po_id', 'categ_id'):
        if name in values:
            integer(values[name], name)
    values.setdefault('type', 'consu')
    if values['type'] not in ('consu', 'service'):
        raise ValueError('type must be consu (goods) or service')
    values.setdefault('tracking', 'none' if values['type'] == 'service' else 'lot')
    if values['tracking'] not in ('none', 'lot', 'serial') or (values['type'] == 'service' and values['tracking'] != 'none'):
        raise ValueError('Invalid product tracking')
    if 'sale_ok' in values and type(values['sale_ok']) is not bool:
        raise ValueError('sale_ok must be boolean')
    return values
