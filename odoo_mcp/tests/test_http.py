import json
from datetime import datetime, timedelta
from unittest.mock import patch

from odoo.tests import HttpCase, tagged
from odoo.tests.common import new_test_user


@tagged('post_install', '-at_install')
class TestMcpHttp(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(cls.env, login='mcp_http', groups='base.group_user,odoo_mcp.group_mcp_crm_write,sales_team.group_sale_salesman')
        cls.token = cls.env['res.users.apikeys'].with_user(cls.user)._generate(None, 'MCP test', datetime.now() + timedelta(days=1))
        cls.headers = {'Authorization': 'Bearer ' + cls.token, 'Content-Type': 'application/json',
                       'Accept': 'application/json, text/event-stream'}

    def rpc(self, method, params=None, **kwargs):
        return self.url_open('/odoo_mcp/mcp', data=json.dumps({'jsonrpc': '2.0', 'id': 1,
            'method': method, 'params': params or {}}), headers=kwargs.get('headers', self.headers))

    def test_initialize_and_tools(self):
        response = self.rpc('initialize', {'protocolVersion': '2025-03-26', 'capabilities': {},
                                          'clientInfo': {'name': 'test', 'version': '1'}})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['result']['protocolVersion'], '2025-03-26')
        tools = self.rpc('tools/list').json()['result']['tools']
        self.assertIn('crm_create', [t['name'] for t in tools])
        result = self.rpc('tools/call', {'name': 'odoo_catalog'}).json()['result']
        self.assertFalse(result['isError'])

    def test_auth_and_origin(self):
        response = self.rpc('ping', headers={**self.headers, 'Authorization': 'Bearer invalid'})
        self.assertEqual(response.status_code, 401)
        response = self.rpc('ping', headers={**self.headers, 'Origin': 'https://untrusted.invalid'})
        self.assertEqual(response.status_code, 403)
        response = self.rpc('ping', headers={k: v for k, v in self.headers.items() if k != 'Authorization'})
        self.assertEqual(response.status_code, 401)

    def test_invalid_requests(self):
        response = self.url_open('/odoo_mcp/mcp', data='[{}]', headers=self.headers)
        self.assertEqual(response.json()['error']['code'], -32600)
        response = self.url_open('/odoo_mcp/mcp', data='{', headers=self.headers)
        self.assertEqual(response.json()['error']['code'], -32700)
        response = self.rpc('ping', headers={**self.headers, 'MCP-Protocol-Version': 'invalid'})
        self.assertEqual(response.status_code, 400)
        response = self.rpc('ping', headers={**self.headers, 'Accept': 'text/html'})
        self.assertEqual(response.status_code, 406)
        response = self.url_open('/odoo_mcp/mcp', headers=self.headers)
        self.assertEqual(response.status_code, 405)

    def test_notification_never_writes(self):
        response = self.url_open('/odoo_mcp/mcp', data=json.dumps({'jsonrpc': '2.0', 'method': 'tools/call',
            'params': {'name': 'crm_create', 'arguments': {'values': {'name': 'Notification must not write'}}}}), headers=self.headers)
        self.assertEqual(response.status_code, 202)
        self.assertFalse(self.env['crm.lead'].search([('name', '=', 'Notification must not write')]))

    def test_tool_errors_and_rollback(self):
        result = self.rpc('tools/call', {'name': 'crm_create', 'arguments': {'values': {'name': 'Invalid', 'company_id': 1}}}).json()['result']
        self.assertTrue(result['isError'])
        result = self.rpc('tools/call', {'name': 'crm_create', 'arguments': {'values': {'name': 'Valid HTTP lead'}}}).json()['result']
        self.assertFalse(result['isError'], result)

    def test_failure_after_write_rolls_back(self):
        gateway_class = type(self.env['odoo.mcp.gateway'])
        original = gateway_class._execute

        def fail_after_write(gateway, name, args):
            result = original(gateway, name, args)
            if name == 'crm_create':
                raise ValueError('Simulated failure after insert')
            return result

        with patch.object(gateway_class, '_execute', fail_after_write):
            result = self.rpc('tools/call', {'name': 'crm_create', 'arguments': {
                'values': {'name': 'Must roll back'}}}).json()['result']
        self.assertTrue(result['isError'])
        self.assertFalse(self.env['crm.lead'].search([('name', '=', 'Must roll back')]))
