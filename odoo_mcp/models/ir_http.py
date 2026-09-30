import re

from werkzeug.datastructures import WWWAuthenticate
from werkzeug.exceptions import Unauthorized

from odoo import models
from odoo.http import request

from ..oauth_utils import OAUTH_SCOPES, OAuthError


class IrHttp(models.AbstractModel):
    _inherit = 'ir.http'

    @classmethod
    def _auth_method_mcp(cls):
        challenge = {}
        try:
            challenge = {'resource_metadata': request.env['odoo.mcp.oauth.grant']._urls()['metadata'],
                         'scope': ' '.join(OAUTH_SCOPES)}
        except OAuthError:
            # Existing API-key clients also work before an administrator configures HTTPS OAuth.
            pass

        def denied():
            raise Unauthorized('A valid MCP access token or Odoo API key is required.',
                               www_authenticate=WWWAuthenticate('Bearer', challenge))

        authorization = request.httprequest.headers.get('Authorization', '')
        match = re.fullmatch(r'Bearer ([^\s]{1,2048})', authorization, re.IGNORECASE)
        if not match:
            denied()
        token = match[1]
        grant = None
        if token.startswith('omcp_at_'):
            try:
                grant = request.env['odoo.mcp.oauth.token']._resolve(token)
            except OAuthError:
                denied()
            if not grant:
                denied()
            uid = grant.user_id.id
        else:
            uid = request.env['res.users.apikeys']._check_credentials(scope='rpc', key=token)
        if not uid or (request.session.uid and request.session.uid != uid):
            denied()
        request.update_env(user=uid, su=False)
        if grant:
            request.update_context(mcp_oauth_scopes=grant.scope.split(),
                                   allowed_company_ids=[grant.company_id.id])
        cls._auth_method_user()
