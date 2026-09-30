from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged
from odoo.tests.common import new_test_user


@tagged('post_install', '-at_install')
class TestGateway(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.reader = new_test_user(cls.env, login='mcp_reader', groups='base.group_user,odoo_mcp.group_mcp_user,sales_team.group_sale_salesman,stock.group_stock_user,account.group_account_readonly,project.group_project_user')
        cls.writer = new_test_user(cls.env, login='mcp_writer', groups='base.group_user,odoo_mcp.group_mcp_crm_write,odoo_mcp.group_mcp_chat_write,odoo_mcp.group_mcp_todo_write,sales_team.group_sale_salesman,project.group_project_user,account.group_account_invoice')
        cls.gateway = cls.env['odoo.mcp.gateway'].with_user(cls.writer)
        cls.lead = cls.env['crm.lead'].create({'name': 'MCP test lead', 'user_id': cls.writer.id})

    def test_reader_cannot_write_even_with_sales_rights(self):
        gateway = self.gateway.with_user(self.reader)
        with self.assertRaises(AccessError):
            gateway._call('crm_create', {'values': {'name': 'Forbidden'}})
        self.assertEqual({t['name'] for t in gateway._tools()}, {'odoo_search', 'odoo_catalog', 'odoo_messages'})

    def test_no_arbitrary_model_method_or_context(self):
        for name in ('sale_write', 'execute', 'unlink', 'invoice_post', 'todo_delete'):
            with self.assertRaises(AccessError):
                self.gateway._call(name, {})
        with self.assertRaises(ValueError):
            self.gateway._call('odoo_search', {'resource': 'sales', 'context': {'sudo': True}})
        with self.assertRaises(ValueError):
            self.gateway._call('crm_update', {'id': self.lead.id, 'values': {'order_ids': [(0, 0, {})]}})

    def test_crm_lifecycle(self):
        result = self.gateway._call('crm_create', {'values': {'name': 'Created through MCP', 'type': 'opportunity'}})
        self.gateway._call('crm_update', {'id': result['id'], 'values': {'expected_revenue': 123, 'description': 'Test'}})
        lead = self.env['crm.lead'].browse(result['id'])
        self.assertEqual(lead.expected_revenue, 123)
        self.gateway._call('crm_action', {'id': lead.id, 'action': 'won'})
        self.assertEqual(lead.probability, 100)
        self.gateway._call('crm_action', {'id': lead.id, 'action': 'lost'})
        self.assertFalse(lead.active)
        self.gateway._call('crm_action', {'id': lead.id, 'action': 'restore'})
        self.assertTrue(lead.active)

    def test_read_resource_fields_exist(self):
        from ..policy import RESOURCES
        for resource, (model, fields) in RESOURCES.items():
            with self.subTest(resource=resource):
                self.assertFalse(set(fields) - set(self.env[model]._fields))

    def test_read_all_resources(self):
        gateway = self.gateway.with_user(self.reader)
        from ..policy import RESOURCES
        for resource in RESOURCES:
            with self.subTest(resource=resource):
                result = gateway._call('odoo_search', {'resource': resource, 'limit': 1})
                self.assertIn('records', result)

    def test_todo_scope(self):
        own = self.env['project.task'].create({'name': 'Own todo', 'user_ids': [(6, 0, self.reader.ids)]})
        other = self.env['project.task'].create({'name': 'Other todo', 'user_ids': [(6, 0, self.writer.ids)]})
        result = self.gateway.with_user(self.reader)._call('odoo_search', {'resource': 'todos'})
        ids = [r['id'] for r in result['records']]
        self.assertIn(own.id, ids)
        self.assertNotIn(other.id, ids)

    def test_chat_membership_and_plain_text(self):
        channel = self.env['discuss.channel'].create({'name': 'Private MCP', 'channel_type': 'group',
            'channel_partner_ids': [(4, self.writer.partner_id.id)]})
        result = self.gateway._call('chat_post', {'target': 'channel', 'id': channel.id, 'body': '<script>test</script>'})
        message = self.env['mail.message'].browse(result['id'])
        self.assertIn('&lt;script&gt;', message.body)
        channel.channel_member_ids.filtered(lambda m: m.partner_id == self.writer.partner_id).unlink()
        with self.assertRaises(AccessError):
            self.gateway._call('chat_post', {'target': 'channel', 'id': channel.id, 'body': 'Forbidden'})

    def test_activity_scope(self):
        activity_type = self.env.ref('mail.mail_activity_data_todo')
        result = self.gateway._call('crm_schedule_activity', {'id': self.lead.id,
            'activity_type_id': activity_type.id, 'summary': 'Follow up', 'date_deadline': '2030-01-01'})
        self.gateway._call('crm_complete_activity', {'id': result['id']})
        self.assertFalse(self.env['mail.activity'].browse(result['id']).exists())

    def test_company_and_record_rules(self):
        other_company = self.env['res.company'].create({'name': 'MCP other company'})
        lead = self.env['crm.lead'].create({'name': 'Other company secret', 'company_id': other_company.id})
        with self.assertRaises(AccessError):
            self.gateway._call('crm_update', {'id': lead.id, 'values': {'name': 'Forbidden'}})
        result = self.gateway._call('odoo_search', {'resource': 'crm', 'filters': [['id', '=', lead.id]]})
        self.assertEqual(result['records'], [])

    def test_todo_lifecycle(self):
        result = self.gateway._call('todo_create', {'values': {'name': 'MCP personal task'}})
        task = self.env['project.task'].browse(result['id'])
        self.assertFalse(task.project_id)
        self.assertEqual(task.user_ids, self.writer)
        self.gateway._call('todo_update', {'id': task.id, 'values': {
            'name': 'Updated task', 'description': 'Details', 'priority': '1',
            'date_deadline': '2030-01-01 12:30:00'}})
        self.assertEqual(task.name, 'Updated task')
        for action, state in [('complete', '1_done'), ('reopen', '01_in_progress'), ('cancel', '1_canceled')]:
            self.gateway._call('todo_action', {'id': task.id, 'action': action})
            self.assertEqual(task.state, state)

    def test_todo_write_boundaries(self):
        reader_gateway = self.gateway.with_user(self.reader)
        for tool, args in [('todo_create', {'values': {'name': 'Denied'}}),
                           ('todo_update', {'id': 1, 'values': {'name': 'Denied'}}),
                           ('todo_action', {'id': 1, 'action': 'complete'})]:
            with self.assertRaises(AccessError):
                reader_gateway._call(tool, args)
        project = self.env['project.project'].create({'name': 'Not a personal task'})
        project_task = self.env['project.task'].create({'name': 'Project task',
            'project_id': project.id, 'user_ids': [(6, 0, self.writer.ids)]})
        other = self.env['project.task'].create({'name': 'Other personal task',
            'user_ids': [(6, 0, self.reader.ids)]})
        for task in (project_task, other):
            with self.assertRaises(AccessError):
                self.gateway._call('todo_update', {'id': task.id, 'values': {'name': 'Denied'}})
            with self.assertRaises(AccessError):
                self.gateway._call('todo_action', {'id': task.id, 'action': 'complete'})
            with self.assertRaises(AccessError):
                self.gateway._call('chat_post', {'target': 'todo', 'id': task.id, 'body': 'Denied'})
        for field in ('project_id', 'parent_id', 'user_ids'):
            with self.assertRaises(ValueError):
                self.gateway._call('todo_create', {'values': {'name': 'Denied', field: 1}})

    def test_record_chatter_preserves_business_fields(self):
        sale = self.env['sale.order'].create({'partner_id': self.writer.partner_id.id,
                                             'user_id': self.writer.id})
        journal = self.env['account.journal'].create({'name': 'MCP invoice journal', 'code': 'MCPI', 'type': 'sale'})
        invoice = self.env['account.move'].create({'move_type': 'out_invoice',
            'journal_id': journal.id, 'partner_id': self.writer.partner_id.id})
        for target, record in [('sale', sale), ('invoice', invoice)]:
            before = (record.state, record.amount_total, record.partner_id.id)
            posted = self.gateway._call('chat_post', {'target': target, 'id': record.id, 'body': 'Internal MCP note'})
            self.assertEqual((record.state, record.amount_total, record.partner_id.id), before)
            message = self.env['mail.message'].browse(posted['id'])
            self.assertEqual(message.subtype_id, self.env.ref('mail.mt_note'))
            messages = self.gateway._call('odoo_messages', {'target': target, 'id': record.id})
            self.assertIn(message.id, [m['id'] for m in messages['records']])
        entry = self.env['account.move'].create({'move_type': 'entry', 'journal_id': journal.id})
        with self.assertRaises(AccessError):
            self.gateway._call('odoo_messages', {'target': 'invoice', 'id': entry.id})
        with self.assertRaises(AccessError):
            self.gateway.with_user(self.reader)._call('chat_post', {'target': 'sale', 'id': sale.id, 'body': 'Denied'})
