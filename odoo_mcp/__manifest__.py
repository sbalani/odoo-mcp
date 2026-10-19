{
    'name': 'Odoo MCP Gateway',
    'version': '18.0.1.3.0',
    'summary': 'Scoped MCP access to sales, stock, purchasing, products, invoices, CRM, Discuss and To-do',
    'license': 'LGPL-3',
    'depends': ['sale_management', 'stock', 'account', 'crm', 'mail', 'project_todo', 'purchase_stock'],
    'data': ['security/groups.xml', 'security/ir.model.access.csv', 'views/oauth_views.xml', 'views/purchase_views.xml'],
    'installable': True,
    'application': False,
}
