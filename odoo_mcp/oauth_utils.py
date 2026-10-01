"""Small, dependency-free primitives for the MCP-only OAuth authorization flow."""
import base64
import hashlib
import re
import secrets
from urllib.parse import urlsplit

OAUTH_SCOPES = ('mcp.read', 'mcp.crm.write', 'mcp.chat.write', 'mcp.todo.write', 'offline_access')
SCOPE_GROUPS = {
    'mcp.read': 'odoo_mcp.group_mcp_user',
    'mcp.crm.write': 'odoo_mcp.group_mcp_crm_write',
    'mcp.chat.write': 'odoo_mcp.group_mcp_chat_write',
    'mcp.todo.write': 'odoo_mcp.group_mcp_todo_write',
}
ACCESS_SECONDS = 900
GRANT_DAYS = 30
CODE_SECONDS = 300
CHATGPT_REDIRECT = 'https://chatgpt.com/connector_platform_oauth_redirect'


class OAuthError(ValueError):
    def __init__(self, code, message, status=400):
        self.code = code
        self.status = status
        super().__init__(message)


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def new_secret(prefix):
    return prefix + secrets.token_urlsafe(48)


def check_pkce(verifier, challenge):
    if not isinstance(verifier, str) or not re.fullmatch(r'[A-Za-z0-9._~-]{43,128}', verifier):
        return False
    actual = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('ascii')).digest()).rstrip(b'=').decode('ascii')
    return secrets.compare_digest(actual, challenge)


def canonical_origin(value):
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in ('', '/')
            or any(c.isspace() for c in value)):
        raise OAuthError('server_error', 'Set web.base.url to the canonical HTTPS origin before enabling OAuth.', 503)
    return value.rstrip('/')


def redirect_uri(value):
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.fragment or len(value) > 2048 or any(c.isspace() for c in value)):
        raise ValueError('Each callback must be an absolute HTTPS URL without credentials or a fragment.')
    return value


def consent_csp(callback_uri):
    """Allow the validated client's callback through form-submit redirects."""
    parsed = urlsplit(redirect_uri(callback_uri))
    # Never interpolate callback paths/queries or CSP syntax into a header.
    if not re.fullmatch(r'[A-Za-z0-9.\-:\[\]]+', parsed.netloc):
        raise OAuthError('invalid_request', 'Invalid callback host.')
    origin = 'https://' + parsed.netloc
    return ("default-src 'none'; style-src 'unsafe-inline'; "
            f"form-action 'self' {origin}; frame-ancestors 'none'; base-uri 'none'")


def scopes(value):
    if not isinstance(value, str) or len(value) > 256:
        raise OAuthError('invalid_scope', 'Invalid scopes.')
    selected = set(value.split())
    if not selected <= set(OAUTH_SCOPES) or 'mcp.read' not in selected:
        raise OAuthError('invalid_scope', 'Request mcp.read and only supported optional scopes.')
    return [scope for scope in OAUTH_SCOPES if scope in selected]
