import base64
import re
import secrets
import time
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from odoo import http
from odoo.exceptions import AccessError
from odoo.http import request

from ..oauth_utils import CODE_SECONDS, OAUTH_SCOPES, SCOPE_GROUPS, OAuthError, digest, scopes
from .mcp import response


def oauth_error(exc):
    return response({'error': exc.code, 'error_description': str(exc)}, exc.status)


def bounded_params(source):
    if request.httprequest.content_length and request.httprequest.content_length > 16384:
        raise OAuthError('invalid_request', 'Request too large.', 413)
    if len(source) > 24 or any(len(source.getlist(k)) != 1 for k in source):
        raise OAuthError('invalid_request', 'Duplicate or excessive parameters.')
    params = source.to_dict()
    if any(len(k) > 128 or len(v) > 4096 for k, v in params.items()):
        raise OAuthError('invalid_request', 'Parameter too long.')
    return params


def get_client(client_id):
    client = request.env['odoo.mcp.oauth.client'].sudo().search([
        ('client_id', '=', client_id), ('active', '=', True)], limit=1)
    if not client or not client.secret_hash:
        raise OAuthError('invalid_client', 'Unknown or disabled OAuth client.', 401)
    return client


def authenticated_client(params):
    client_id, secret = params.get('client_id', ''), params.get('client_secret', '')
    authorization = request.httprequest.headers.get('Authorization', '')
    if authorization:
        if client_id or secret or not authorization.startswith('Basic '):
            raise OAuthError('invalid_client', 'Use one supported client authentication method.', 401)
        try:
            decoded = base64.b64decode(authorization[6:], validate=True).decode('utf-8')
            client_id, secret = (unquote(part) for part in decoded.split(':', 1))
        except (ValueError, UnicodeDecodeError):
            raise OAuthError('invalid_client', 'Invalid client authentication.', 401) from None
    client = get_client(client_id)
    if not client._check_secret(secret):
        raise OAuthError('invalid_client', 'Invalid client authentication.', 401)
    return client


def callback(uri, **values):
    parsed = urlsplit(uri)
    values['iss'] = request.env['odoo.mcp.oauth.grant']._urls()['issuer']
    query = parse_qsl(parsed.query, keep_blank_values=True) + list(values.items())
    res = request.redirect(urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), '')), local=False)
    res.headers['Cache-Control'] = 'no-store'
    res.headers['Referrer-Policy'] = 'no-referrer'
    return res


class McpOAuth(http.Controller):
    @http.route('/.well-known/oauth-protected-resource/odoo_mcp/mcp', type='http', auth='public',
                methods=['GET'], save_session=False)
    def resource_metadata(self, **unused):
        try:
            urls = request.env['odoo.mcp.oauth.grant']._urls()
            return response({'resource': urls['resource'], 'authorization_servers': [urls['issuer']],
                             'scopes_supported': list(OAUTH_SCOPES), 'bearer_methods_supported': ['header']})
        except OAuthError as exc:
            return oauth_error(exc)

    @http.route('/.well-known/oauth-authorization-server/odoo_mcp/oauth', type='http', auth='public',
                methods=['GET'], save_session=False)
    def authorization_metadata(self, **unused):
        try:
            urls = request.env['odoo.mcp.oauth.grant']._urls()
            return response({'issuer': urls['issuer'],
                'authorization_endpoint': urls['issuer'] + '/authorize',
                'token_endpoint': urls['issuer'] + '/token',
                'revocation_endpoint': urls['issuer'] + '/revoke',
                'response_types_supported': ['code'], 'grant_types_supported': ['authorization_code', 'refresh_token'],
                'token_endpoint_auth_methods_supported': ['client_secret_post', 'client_secret_basic'],
                'revocation_endpoint_auth_methods_supported': ['client_secret_post', 'client_secret_basic'],
                'code_challenge_methods_supported': ['S256'], 'scopes_supported': list(OAUTH_SCOPES),
                'authorization_response_iss_parameter_supported': True})
        except OAuthError as exc:
            return oauth_error(exc)

    @http.route('/odoo_mcp/oauth/authorize', type='http', auth='user', methods=['GET'])
    def authorize(self, **unused):
        try:
            request.env['odoo.mcp.gateway']._check_member()
            params = bounded_params(request.httprequest.args)
            urls = request.env['odoo.mcp.oauth.grant']._urls()
            client = get_client(params.get('client_id', ''))
            if params.get('redirect_uri') not in client.redirect_uris.splitlines():
                raise OAuthError('invalid_request', 'Callback URL is not registered for this client.')
            if params.get('response_type') != 'code':
                raise OAuthError('unsupported_response_type', 'Only authorization code flow is supported.')
            if params.get('resource') != urls['resource']:
                raise OAuthError('invalid_target', 'The resource must exactly match the MCP endpoint URL.')
            if params.get('code_challenge_method') != 'S256' or not re.fullmatch(r'[A-Za-z0-9_-]{43}', params.get('code_challenge', '')):
                raise OAuthError('invalid_request', 'PKCE S256 is required.')
            if not params.get('state') or len(params['state']) > 2048:
                raise OAuthError('invalid_request', 'A nonempty state parameter is required.')
            requested = scopes(params.get('scope', 'mcp.read'))
            granted = [s for s in requested if s == 'offline_access' or request.env.user.has_group(SCOPE_GROUPS[s])]
            pending = {k: params[k] for k in ('client_id', 'redirect_uri', 'resource', 'code_challenge', 'state')}
            pending.update({'scope': ' '.join(granted), 'user_id': request.env.uid,
                            'company_id': request.env.user.company_id.id, 'expires': time.time() + CODE_SECONDS})
            requests = {k: v for k, v in request.session.get('odoo_mcp_oauth_requests', {}).items()
                        if v['expires'] > time.time()}
            if len(requests) >= 10:
                raise OAuthError('temporarily_unavailable', 'Too many pending consent requests; try again shortly.', 429)
            nonce = secrets.token_urlsafe(32)
            requests[nonce] = pending
            request.session['odoo_mcp_oauth_requests'] = requests
            res = request.render('odoo_mcp.oauth_consent', {
                'client_name': client.name, 'user_name': request.env.user.name,
                'company_name': request.env.user.company_id.name, 'nonce': nonce,
                'granted_scopes': granted,
            })
            res.headers.update({'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
                                'X-Frame-Options': 'DENY',
                                'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"})
            return res
        except AccessError:
            return response({'error': 'access_denied', 'error_description': 'This Odoo user needs MCP read access.'}, 403)
        except OAuthError as exc:
            return oauth_error(exc)

    @http.route('/odoo_mcp/oauth/consent', type='http', auth='user', methods=['POST'], csrf=True)
    def consent(self, **unused):
        try:
            request.env['odoo.mcp.gateway']._check_member()
            params = bounded_params(request.httprequest.form)
            requests = dict(request.session.get('odoo_mcp_oauth_requests', {}))
            pending = requests.pop(params.get('nonce', ''), None)
            request.session['odoo_mcp_oauth_requests'] = requests
            if not pending or pending['expires'] <= time.time() or pending['user_id'] != request.env.uid:
                raise OAuthError('invalid_request', 'Consent expired or already used. Start connecting again.')
            client = get_client(pending['client_id'])
            if pending['redirect_uri'] not in client.redirect_uris.splitlines():
                raise OAuthError('invalid_request', 'Callback URL is no longer registered.')
            if pending['resource'] != request.env['odoo.mcp.oauth.grant']._urls()['resource']:
                raise OAuthError('invalid_target', 'MCP URL changed. Start connecting again.')
            company = request.env['res.company'].browse(pending['company_id'])
            if company not in request.env.user.company_ids:
                raise OAuthError('access_denied', 'Company access was removed.', 403)
            if params.get('decision') == 'deny':
                return callback(pending['redirect_uri'], error='access_denied', state=pending['state'])
            if params.get('decision') != 'approve':
                raise OAuthError('invalid_request', 'An explicit consent decision is required.')
            # Remove rights withdrawn while the consent screen was open; never add new rights silently.
            pending['scope'] = ' '.join(s for s in pending['scope'].split()
                if s == 'offline_access' or request.env.user.has_group(SCOPE_GROUPS[s]))
            code = request.env['odoo.mcp.oauth.grant']._new(client, request.env.user, company, pending)
            return callback(pending['redirect_uri'], code=code, state=pending['state'])
        except AccessError:
            return response({'error': 'access_denied'}, 403)
        except OAuthError as exc:
            return oauth_error(exc)

    @http.route('/odoo_mcp/oauth/token', type='http', auth='public', methods=['POST'], csrf=False, save_session=False)
    def token(self, **unused):
        try:
            if request.httprequest.mimetype != 'application/x-www-form-urlencoded':
                raise OAuthError('invalid_request', 'Use application/x-www-form-urlencoded.', 415)
            params = bounded_params(request.httprequest.form)
            client = authenticated_client(params)
            grants = request.env['odoo.mcp.oauth.grant']
            if params.get('grant_type') == 'authorization_code':
                return response(grants._exchange_code(client, params))
            if params.get('grant_type') == 'refresh_token':
                return response(grants._refresh(client, params))
            raise OAuthError('unsupported_grant_type', 'Only authorization_code and refresh_token are supported.')
        except OAuthError as exc:
            # Preserve intentional revocations on code/refresh replay. No blanket rollback here.
            return oauth_error(exc)

    @http.route('/odoo_mcp/oauth/revoke', type='http', auth='public', methods=['POST'], csrf=False, save_session=False)
    def revoke(self, **unused):
        try:
            if request.httprequest.mimetype != 'application/x-www-form-urlencoded':
                raise OAuthError('invalid_request', 'Use application/x-www-form-urlencoded.', 415)
            params = bounded_params(request.httprequest.form)
            client = authenticated_client(params)
            token = request.env['odoo.mcp.oauth.token'].sudo().search([
                ('token_hash', '=', digest(params.get('token', ''))), ('grant_id.client_id', '=', client.id)], limit=1)
            if token:
                token.grant_id._lock()
                token.grant_id.revoked = True
            return response({})
        except OAuthError as exc:
            return oauth_error(exc)
