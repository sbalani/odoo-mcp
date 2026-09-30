{
    'name': 'Odoo MCP Gateway',
    'version': '18.0.1.1.0',
    'summary': 'Scoped MCP access to sales, stock, invoices, CRM, Discuss and To-do',
    'license': 'LGPL-3',
    'depends': ['sale_management', 'stock', 'account', 'crm', 'mail', 'project_todo'],
    'data': ['security/groups.xml'],
    'installable': True,
    'application': False,
}
