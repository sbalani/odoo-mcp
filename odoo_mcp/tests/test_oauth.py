import base64
import hashlib
import json
from datetime import timedelta
from urllib.parse import parse_qs, urlencode, urlsplit

from lxml import html

from odoo import Command, fields
from odoo.exceptions import AccessError
from odoo.tests import HttpCase, tagged
from odoo.tests.common import new_test_user

from ..oauth_utils import CHATGPT_REDIRECT, OAUTH_SCOPES, digest


@tagged('post_install', '-at_install')
class TestMcpOAuth(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['ir.config_parameter'].sudo().set_param('web.base.url', 'https://odoo.example.test')
        cls.env['ir.config_parameter'].sudo().set_param('web.base.url.freeze', 'True')
        cls.user = new_test_user(cls.env, login='mcp_oauth', password='oauth-test-password',
            groups='base.group_user,odoo_mcp.group_mcp_crm_write,odoo_mcp.group_mcp_chat_write,odoo_mcp.group_mcp_todo_write,sales_team.group_sale_salesman')
        cls.secret = 'oauth-client-fixture-secret-not-for-production'
        cls.client = cls.env['odoo.mcp.oauth.client'].sudo().create({
            'name': 'ChatGPT test', 'active': True, 'secret_hash': digest(cls.secret),
            'redirect_uris': CHATGPT_REDIRECT})
        cls.resource = 'https://odoo.example.test/odoo_mcp/mcp'
        cls.issuer = 'https://odoo.example.test/odoo_mcp/oauth'
        cls.verifier = 'test-verifier-with-enough-entropy-for-fixtures-123456789'
        cls.challenge = base64.urlsafe_b64encode(hashlib.sha256(cls.verifier.encode()).digest()).rstrip(b'=').decode()

    def auth_params(self, scope=' '.join(OAUTH_SCOPES), **updates):
        return {'client_id': self.client.client_id, 'response_type': 'code',
            'redirect_uri': CHATGPT_REDIRECT, 'resource': self.resource, 'state': 'client-state',
            'scope': scope, 'code_challenge': self.challenge, 'code_challenge_method': 'S256', **updates}

    def consent_form(self, params=None):
        res = self.url_open('/odoo_mcp/oauth/authorize?' + urlencode(params or self.auth_params()))
        self.assertEqual(res.status_code, 200, res.text[:1500])
        self.assertIn("form-action 'self' https://chatgpt.com;", res.headers['Content-Security-Policy'])
        doc = html.fromstring(res.text)
        return {e.name: e.value for e in doc.xpath('//input')}, res

    def authorize(self, scope=' '.join(OAUTH_SCOPES)):
        self.authenticate(self.user.login, 'oauth-test-password')
        form, res = self.consent_form(self.auth_params(scope))
        self.assertIn('Allow connection', res.text)
        form['decision'] = 'approve'
        res = self.url_open('/odoo_mcp/oauth/consent', data=form, allow_redirects=False)
        self.assertEqual(res.status_code, 303, res.text[:1500])
        params = parse_qs(urlsplit(res.headers['Location']).query)
        self.assertEqual(params['state'], ['client-state'])
        self.assertEqual(params['iss'], [self.issuer])
        return params['code'][0]

    def token(self, code=None, **updates):
        data = {'client_id': self.client.client_id, 'client_secret': self.secret,
            'grant_type': 'authorization_code', 'code': code or '', 'redirect_uri': CHATGPT_REDIRECT,
            'resource': self.resource, 'code_verifier': self.verifier, **updates}
        return self.url_open('/odoo_mcp/oauth/token', data=data)

    def access(self, token, method='tools/list', params=None):
        return self.url_open('/odoo_mcp/mcp', data=json.dumps({'jsonrpc': '2.0', 'id': 1,
            'method': method, 'params': params or {}}), headers={
                'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json',
                'Accept': 'application/json, text/event-stream'})

    def mint(self, scope=' '.join(OAUTH_SCOPES)):
        code = self.authorize(scope)
        res = self.token(code)
        self.assertEqual(res.status_code, 200, res.text[:1500])
        return res.json(), code

    def test_discovery_and_unauthenticated_challenge(self):
        res = self.url_open('/.well-known/oauth-protected-resource/odoo_mcp/mcp')
        self.assertEqual(res.json()['resource'], self.resource)
        self.assertEqual(res.json()['authorization_servers'], [self.issuer])
        res = self.url_open('/.well-known/oauth-authorization-server/odoo_mcp/oauth')
        self.assertEqual(res.json()['code_challenge_methods_supported'], ['S256'])
        self.assertTrue(res.json()['authorization_response_iss_parameter_supported'])
        self.assertNotIn('registration_endpoint', res.json())
        res = self.url_open('/odoo_mcp/mcp', data='{}', headers={'Content-Type': 'application/json'})
        self.assertEqual(res.status_code, 401)
        self.assertIn('resource_metadata=', res.headers['WWW-Authenticate'])

    def test_full_consent_token_catalog_and_hashed_storage(self):
        tokens, code = self.mint()
        res = self.access(tokens['access_token'], 'initialize', {'protocolVersion': '2025-11-25',
            'capabilities': {}, 'clientInfo': {'name': 'OAuth test', 'version': '1'}})
        self.assertEqual(res.status_code, 200, res.text)
        res = self.access(tokens['access_token'])
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(len(res.json()['result']['tools']), 12)
        res = self.access(tokens['access_token'], 'tools/call', {'name': 'odoo_catalog', 'arguments': {}})
        self.assertFalse(res.json()['result']['isError'])
        grant = self.env['odoo.mcp.oauth.grant'].sudo().search([('code_hash', '=', digest(code))])
        self.assertEqual(grant.user_id, self.user)
        stored = self.env['odoo.mcp.oauth.token'].sudo().search([('grant_id', '=', grant.id)])
        self.assertEqual(set(stored.mapped('token_hash')), {digest(tokens['access_token']), digest(tokens['refresh_token'])})
        self.assertNotIn(code, grant.code_hash)

    def test_authorization_rejects_redirect_pkce_scope_and_resource(self):
        self.authenticate(self.user.login, 'oauth-test-password')
        for update in ({'redirect_uri': 'https://attacker.invalid/callback'},
                       {'redirect_uri': CHATGPT_REDIRECT + '/extra'},
                       {'resource': 'https://other.example/mcp'}, {'code_challenge_method': 'plain'},
                       {'code_challenge': ''}, {'scope': 'mcp.read admin'}, {'state': ''}):
            res = self.url_open('/odoo_mcp/oauth/authorize?' + urlencode(self.auth_params(**update)), allow_redirects=False)
            self.assertEqual(res.status_code, 400, res.text)
            self.assertNotIn('Location', res.headers)

    def test_csrf_consent_denial_and_no_automatic_grant(self):
        self.authenticate(self.user.login, 'oauth-test-password')
        before = self.env['odoo.mcp.oauth.grant'].sudo().search_count([])
        form, res = self.consent_form()
        self.assertEqual(self.env['odoo.mcp.oauth.grant'].sudo().search_count([]), before)
        res = self.url_open('/odoo_mcp/oauth/consent', data={'nonce': form['nonce'], 'decision': 'approve'})
        self.assertEqual(res.status_code, 400)
        res = self.url_open('/odoo_mcp/oauth/consent', data={**form, 'decision': 'deny'}, allow_redirects=False)
        self.assertEqual(res.status_code, 303)
        query = parse_qs(urlsplit(res.headers['Location']).query)
        self.assertEqual(query['error'], ['access_denied'])
        self.assertEqual(query['iss'], [self.issuer])
        self.assertEqual(self.env['odoo.mcp.oauth.grant'].sudo().search_count([]), before)
        res = self.url_open('/odoo_mcp/oauth/consent', data={**form, 'decision': 'approve'}, allow_redirects=False)
        self.assertEqual(res.status_code, 400)

    def test_code_bindings_secret_basic_auth_and_code_replay(self):
        code = self.authorize()
        for change in ({'code_verifier': 'wrong'}, {'resource': 'https://other.example/mcp'},
                       {'redirect_uri': 'https://attacker.invalid/callback'}, {'client_secret': 'wrong'}):
            res = self.token(code, **change)
            self.assertIn(res.status_code, (400, 401), res.text)
        basic = base64.b64encode((self.client.client_id + ':' + self.secret).encode()).decode()
        res = self.url_open('/odoo_mcp/oauth/token', data={
            'grant_type': 'authorization_code', 'code': code, 'redirect_uri': CHATGPT_REDIRECT,
            'resource': self.resource, 'code_verifier': self.verifier}, headers={'Authorization': 'Basic ' + basic})
        self.assertEqual(res.status_code, 200, res.text)
        access = res.json()['access_token']
        res = self.token(code)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.access(access).status_code, 401)

    def test_refresh_rotation_reuse_and_revoke(self):
        tokens, _ = self.mint()
        res = self.token(grant_type='refresh_token', refresh_token=tokens['refresh_token'])
        self.assertEqual(res.status_code, 200, res.text)
        renewed = res.json()
        self.assertNotEqual(tokens['refresh_token'], renewed['refresh_token'])
        self.assertEqual(self.access(renewed['access_token']).status_code, 200)
        self.assertEqual(self.token(grant_type='refresh_token', refresh_token=tokens['refresh_token']).status_code, 400)
        self.assertEqual(self.access(renewed['access_token']).status_code, 401)
        tokens, _ = self.mint()
        res = self.url_open('/odoo_mcp/oauth/revoke', data={'client_id': self.client.client_id,
            'client_secret': self.secret, 'token': tokens['refresh_token']})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.access(tokens['access_token']).status_code, 401)

    def test_read_only_consent_cannot_use_write_tools_or_general_api(self):
        tokens, _ = self.mint('mcp.read')
        self.assertNotIn('refresh_token', tokens)
        res = self.access(tokens['access_token'])
        self.assertEqual({t['name'] for t in res.json()['result']['tools']}, {'odoo_search', 'odoo_catalog', 'odoo_messages'})
        for name in ('crm_create', 'chat_post', 'todo_create'):
            res = self.access(tokens['access_token'], 'tools/call', {'name': name, 'arguments': {}})
            self.assertEqual(res.json()['error']['code'], -32602)
        uid = self.env['res.users.apikeys']._check_credentials(scope='rpc', key=tokens['access_token'])
        self.assertFalse(uid)

    def test_expired_code_token_grant_and_removed_permissions(self):
        code = self.authorize()
        grant = self.env['odoo.mcp.oauth.grant'].sudo().search([('code_hash', '=', digest(code))])
        grant.code_expires_at = fields.Datetime.now() - timedelta(seconds=1)
        self.assertEqual(self.token(code).status_code, 400)
        tokens, _ = self.mint()
        token = self.env['odoo.mcp.oauth.token'].sudo().search([('token_hash', '=', digest(tokens['access_token']))])
        token.expires_at = fields.Datetime.now() - timedelta(seconds=1)
        self.assertEqual(self.access(tokens['access_token']).status_code, 401)
        token.grant_id.expires_at = fields.Datetime.now() - timedelta(seconds=1)
        self.assertEqual(self.token(grant_type='refresh_token', refresh_token=tokens['refresh_token']).status_code, 400)
        tokens, _ = self.mint()
        self.user.groups_id = [Command.unlink(self.env.ref('odoo_mcp.group_mcp_crm_write').id)]
        res = self.access(tokens['access_token'])
        self.assertNotIn('crm_create', {t['name'] for t in res.json()['result']['tools']})
        self.client.active = False
        self.assertEqual(self.access(tokens['access_token']).status_code, 401)

    def test_non_admin_cannot_read_oauth_storage(self):
        for model in ('odoo.mcp.oauth.client', 'odoo.mcp.oauth.grant', 'odoo.mcp.oauth.token'):
            with self.assertRaises(AccessError):
                self.env[model].with_user(self.user).search_read([], ['id'])

    def test_login_required_and_user_without_mcp_group_cannot_consent(self):
        res = self.url_open('/odoo_mcp/oauth/authorize?' + urlencode(self.auth_params()), allow_redirects=False)
        self.assertIn(res.status_code, (302, 303))
        self.assertIn('/web/login', res.headers['Location'])
        user = new_test_user(self.env, login='mcp_oauth_denied', password='oauth-denied', groups='base.group_user')
        self.authenticate(user.login, 'oauth-denied')
        res = self.url_open('/odoo_mcp/oauth/authorize?' + urlencode(self.auth_params()), allow_redirects=False)
        self.assertEqual(res.status_code, 403)
