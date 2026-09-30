"""OAuth state is inaccessible through RPC to non-administrators.

Only private methods and the consent/token controllers mint credentials. Business
records always use the authenticated non-sudo environment, never this storage env.
"""
import secrets
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError

from ..oauth_utils import (ACCESS_SECONDS, CHATGPT_REDIRECT, CODE_SECONDS, GRANT_DAYS,
                           OAuthError, canonical_origin, check_pkce, digest, new_secret,
                           redirect_uri)


class OAuthClient(models.Model):
    _name = 'odoo.mcp.oauth.client'
    _description = 'MCP OAuth Client'

    name = fields.Char(required=True, default='ChatGPT')
    active = fields.Boolean(default=False)
    client_id = fields.Char(required=True, readonly=True, copy=False,
                            default=lambda self: new_secret('omcp_client_'), index=True)
    secret_hash = fields.Char(readonly=True, copy=False, groups='base.group_system')
    redirect_uris = fields.Text(required=True, default=CHATGPT_REDIRECT,
        help='Exact HTTPS callbacks, one per line. No wildcards. Copy the callback shown by the client.')
    server_url = fields.Char(compute='_compute_urls')
    authorization_url = fields.Char(compute='_compute_urls')
    token_url = fields.Char(compute='_compute_urls')

    _sql_constraints = [('client_id_unique', 'unique(client_id)', 'OAuth client IDs must be unique.')]

    def _compute_urls(self):
        base = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '').rstrip('/')
        for client in self:
            client.server_url = base + '/odoo_mcp/mcp'
            client.authorization_url = base + '/odoo_mcp/oauth/authorize'
            client.token_url = base + '/odoo_mcp/oauth/token'

    @api.constrains('redirect_uris')
    def _check_redirect_uris(self):
        for client in self:
            uris = client.redirect_uris.splitlines()
            if not uris or len(uris) > 10:
                raise ValidationError('Specify between one and ten exact HTTPS callback URLs.')
            try:
                for uri in uris:
                    redirect_uri(uri)
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc

    def action_generate_secret(self):
        self.ensure_one()
        if not self.env.user.has_group('base.group_system'):
            raise AccessError('Only administrators can configure OAuth clients.')
        self.check_access('write')
        self.env['odoo.mcp.oauth.grant']._urls()  # Fail closed until HTTPS is configured.
        secret = new_secret('omcp_client_secret_')
        self.write({'secret_hash': digest(secret), 'active': True})
        self.env['odoo.mcp.oauth.grant'].sudo().search([('client_id', '=', self.id)]).write({'revoked': True})
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'title': 'Copy this OAuth client secret now (shown only once)',
            'message': secret, 'type': 'warning', 'sticky': True,
        }}

    def _check_secret(self, secret):
        return bool(self.active and self.secret_hash and isinstance(secret, str)
                    and secrets.compare_digest(self.secret_hash, digest(secret)))


class OAuthGrant(models.Model):
    _name = 'odoo.mcp.oauth.grant'
    _description = 'MCP OAuth Consent'
    _rec_name = 'user_id'
    _order = 'create_date desc'

    client_id = fields.Many2one('odoo.mcp.oauth.client', required=True, ondelete='cascade', index=True)
    user_id = fields.Many2one('res.users', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one('res.company', required=True, ondelete='cascade')
    issuer = fields.Char(required=True)
    resource = fields.Char(required=True)
    scope = fields.Char(required=True)
    redirect_uri = fields.Char(required=True)
    expires_at = fields.Datetime(required=True)
    revoked = fields.Boolean(default=False, index=True)
    code_hash = fields.Char(required=True, index=True, groups='base.group_system')
    code_challenge = fields.Char(required=True, groups='base.group_system')
    code_expires_at = fields.Datetime(required=True)
    code_used = fields.Boolean(default=False)

    _sql_constraints = [('code_hash_unique', 'unique(code_hash)', 'Authorization codes must be unique.')]

    def action_revoke(self):
        if not self.env.user.has_group('base.group_system'):
            raise AccessError('Only administrators can revoke grants through this screen.')
        self.write({'revoked': True})

    def _urls(self):
        base = canonical_origin(self.env['ir.config_parameter'].sudo().get_param('web.base.url', ''))
        return {'base': base, 'issuer': base + '/odoo_mcp/oauth', 'resource': base + '/odoo_mcp/mcp',
                'metadata': base + '/.well-known/oauth-protected-resource/odoo_mcp/mcp'}

    def _live(self):
        self.ensure_one()
        urls = self._urls()
        user = self.user_id
        return (not self.revoked and self.expires_at > fields.Datetime.now()
                and self.client_id.active and bool(self.client_id.secret_hash)
                and self.issuer == urls['issuer'] and self.resource == urls['resource']
                and user.active and not user.share and user.id != 1
                and self.company_id in user.company_ids
                and user.has_group('odoo_mcp.group_mcp_user'))

    def _lock(self):
        self.ensure_one()
        self.flush_recordset()
        self.env.cr.execute('SELECT id FROM odoo_mcp_oauth_grant WHERE id = %s FOR UPDATE', [self.id])
        self.invalidate_recordset()

    def _new(self, client, user, company, pending):
        code = new_secret('omcp_code_')
        now = fields.Datetime.now()
        self.sudo().create({
            'client_id': client.id, 'user_id': user.id, 'company_id': company.id,
            'issuer': self._urls()['issuer'], 'resource': pending['resource'],
            'scope': pending['scope'], 'redirect_uri': pending['redirect_uri'],
            'expires_at': now + timedelta(days=GRANT_DAYS), 'code_hash': digest(code),
            'code_challenge': pending['code_challenge'], 'code_expires_at': now + timedelta(seconds=CODE_SECONDS),
        })
        return code

    def _exchange_code(self, client, params):
        code = params.get('code', '')
        grant = self.sudo().search([('client_id', '=', client.id), ('code_hash', '=', digest(code))], limit=1)
        if not grant:
            raise OAuthError('invalid_grant', 'Invalid or expired authorization code.')
        grant._lock()
        if grant.code_used:
            grant.revoked = True
            raise OAuthError('invalid_grant', 'Authorization code already used; reconnect.')
        if (not grant._live() or grant.code_expires_at <= fields.Datetime.now()
                or params.get('redirect_uri') != grant.redirect_uri
                or params.get('resource') != grant.resource
                or not check_pkce(params.get('code_verifier'), grant.code_challenge)):
            raise OAuthError('invalid_grant', 'Invalid or expired authorization code.')
        grant.code_used = True
        return grant._mint()

    def _refresh(self, client, params):
        token = self.env['odoo.mcp.oauth.token'].sudo().search([
            ('token_hash', '=', digest(params.get('refresh_token', ''))), ('kind', '=', 'refresh'),
            ('grant_id.client_id', '=', client.id)], limit=1)
        if not token:
            raise OAuthError('invalid_grant', 'Invalid or expired refresh token.')
        grant = token.grant_id
        grant._lock()
        token.invalidate_recordset()
        if token.used:
            grant.revoked = True
            raise OAuthError('invalid_grant', 'Refresh token already used; reconnect.')
        if (not grant._live() or token.expires_at <= fields.Datetime.now()
                or params.get('resource', grant.resource) != grant.resource
                or params.get('scope', grant.scope) != grant.scope):
            raise OAuthError('invalid_grant', 'Invalid refresh request.')
        token.used = True
        return grant._mint()

    def _mint(self):
        self.ensure_one()
        now = fields.Datetime.now()
        expiry = min(now + timedelta(seconds=ACCESS_SECONDS), self.expires_at)
        access = new_secret('omcp_at_')
        vals = [{'grant_id': self.id, 'kind': 'access', 'token_hash': digest(access), 'expires_at': expiry}]
        result = {'access_token': access, 'token_type': 'Bearer',
                  'expires_in': max(0, int((expiry - now).total_seconds())), 'scope': self.scope}
        if 'offline_access' in self.scope.split():
            refresh = new_secret('omcp_rt_')
            vals.append({'grant_id': self.id, 'kind': 'refresh', 'token_hash': digest(refresh), 'expires_at': self.expires_at})
            result['refresh_token'] = refresh
        self.env['odoo.mcp.oauth.token'].sudo().create(vals)
        return result

    @api.autovacuum
    def _gc_expired_grants(self):
        self.sudo().search([('expires_at', '<', fields.Datetime.now() - timedelta(days=7))]).unlink()


class OAuthToken(models.Model):
    _name = 'odoo.mcp.oauth.token'
    _description = 'Hashed MCP OAuth Token'

    grant_id = fields.Many2one('odoo.mcp.oauth.grant', required=True, ondelete='cascade', index=True)
    token_hash = fields.Char(required=True, index=True)
    kind = fields.Selection([('access', 'Access'), ('refresh', 'Refresh')], required=True)
    expires_at = fields.Datetime(required=True)
    used = fields.Boolean(default=False)

    _sql_constraints = [('token_hash_unique', 'unique(token_hash)', 'OAuth tokens must be unique.')]

    def _resolve(self, value):
        token = self.sudo().search([('token_hash', '=', digest(value)), ('kind', '=', 'access')], limit=1)
        if not token or token.expires_at <= fields.Datetime.now() or not token.grant_id._live():
            return None
        return token.grant_id
