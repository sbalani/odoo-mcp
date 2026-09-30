# Odoo MCP Gateway (Odoo 18)

An installable Odoo addon exposing a small, explicitly scoped Model Context
Protocol server. Deploy this repository as a custom addon repository in
Cloudpepper; no separate Python service or third-party Python dependency is needed.

## Access scope

| Area | Supported operations |
| --- | --- |
| Sales and order lines | Read only |
| Inventory, products, warehouses, locations, transfers, stock move lines | Read only |
| Lots and traceability | Read only |
| Customer invoices, supplier bills, credit notes and invoice lines | Read only |
| CRM | Read; create/update leads and opportunities; won/lost; archive/restore; schedule/complete activities |
| Discuss | Read joined channels and messages; post to joined channels |
| Record chatter (CRM, sales, invoices, lots, transfers, To-do) | Read; post internal notes with normal Odoo posting permissions |
| To-do | Read, create, edit, complete, reopen and cancel personal tasks assigned to the authenticated user, with no project |
| Contacts, users, CRM stages/teams and activity types | Read selected reference fields |

Invoice business fields remain read-only. To-do writes require a separate MCP group. There is no generic model access,
method execution, delete, SQL, arbitrary context, relation traversal, field
discovery, or write access to sales/stock/accounting business fields. There are no sync hooks,
manufacturing operations or dependencies on other custom connectors. Expired OAuth state is cleaned up by Odoo autovacuum.
CRM/contact references use existing IDs; the addon never creates contacts.
Record chatter writes add internal notes only; they do not change business fields or document states. Stock quants and other models without supported chatter are excluded. To-dos cannot be reassigned or converted to project tasks through MCP.

## Cloudpepper installation

1. Add `https://github.com/sbalani/odoo-mcp.git` as a custom addons Git repository,
   selecting branch `18.0`. The repository root contains the `odoo_mcp` addon.
2. Pull/rebuild the Odoo instance through Cloudpepper. Update the Apps list in
   developer mode, remove the Apps-only filter, and install **Odoo MCP Gateway**.
   Its dependencies include Sales, Inventory, Accounting, CRM, Discuss and To-do.
3. Create a dedicated **internal** integration user (never the superuser).
   Grant the ordinary Odoo application permissions required for the records it
   should access. MCP groups do not grant Sales, Accounting or other application rights.
4. In developer mode, under Settings → Users & Companies → Groups, assign:
   - **MCP: Read access** for all allowed reads;
   - **MCP: CRM write access** for CRM mutations;
   - **MCP: Chat write access** for Discuss messages and record notes;
   - **MCP: To-do write access** for personal To-do mutations.
5. For ChatGPT web, use the **OAuth setup below** instead of an API key. For
   clients with custom-header support, generate an Odoo API key under that user's account security settings.
   Use a defined expiration and rotate/revoke keys through Odoo.
6. Connect a client supporting **Streamable HTTP with a custom Bearer header**:
   - URL: `https://YOUR-ODOO-HOST/odoo_mcp/mcp`
   - Header: `Authorization: Bearer YOUR-ODOO-API-KEY`
   - Header: `Accept: application/json, text/event-stream`

Cloudpepper must route the hostname to the correct database (`dbfilter` for
multi-database servers), preserve Authorization headers, and serve HTTPS.
Pushes make the addon available in GitHub; Cloudpepper still needs its configured
pull/rebuild and initial installation. An updated addon may require an Apps upgrade.

The server supports **Odoo API keys** for custom-header clients and **OAuth
with authorization code + PKCE** for ChatGPT web. Tokens/keys are never accepted
in URL query parameters. Legacy HTTP+SSE and stdio transports are not provided.

## ChatGPT web: OAuth setup (18.0.1.2.0+)

1. Pull branch `18.0`, restart/rebuild Odoo, and **upgrade Odoo MCP Gateway in Apps**.
   Upgrading loads the new OAuth models, access controls, and administration menu.
2. As an Odoo administrator, ensure **Settings → Technical → Parameters → System
   Parameters → `web.base.url`** is the canonical HTTPS origin, for example
   `https://YOUR-ODOO-HOST` (no path or query). Set `web.base.url.freeze` to `True`
   to keep alternate login hosts from changing OAuth's issuer. The hostname must
   route to this database without a `?db=` parameter.
3. Open **Settings → MCP → OAuth Clients → ChatGPT**. This pre-created client starts
   disabled. Click **Generate / Rotate Client Secret**, confirm, and copy the secret
   from the sticky notification. It is shown only once and stored only as a hash.
   Copy the **Client ID** from the form too. Generating the secret enables the client.
4. In ChatGPT web, create the custom MCP connection using:
   - **MCP URL:** `https://YOUR-ODOO-HOST/odoo_mcp/mcp`
   - **Authentication:** OAuth
   - **OAuth Client ID / Client Secret:** values from step 3
   - If manual endpoint fields appear, **Authorization URL:**
     `https://YOUR-ODOO-HOST/odoo_mcp/oauth/authorize`; **Token URL:**
     `https://YOUR-ODOO-HOST/odoo_mcp/oauth/token`
   - Use the pre-registered/static client option, if offered. Leave dynamic client
     registration and CIMD off: this addon deliberately supports pre-registration.
5. The default allowed callback is
   `https://chatgpt.com/connector_platform_oauth_redirect`. The server advertises
   issuer identification and returns `iss` in success and denial callbacks. If
   ChatGPT displays a different callback, replace/add that **exact HTTPS URL** in
   the client's Allowed callbacks field. No wildcards are accepted.
6. Connect in ChatGPT, sign in to Odoo **as the intended MCP user**, and review the
   consent screen. This user must already have the normal application permissions
   and MCP groups described above. Click **Allow connection**. ChatGPT receives
   scoped tokens, not the Odoo password or a general-purpose Odoo API key.
7. Select the Odoo connection in a new ChatGPT conversation and ask it to run
   `odoo_catalog`. Depending on the consenting user's groups/scopes, it will see
   up to 12 tools.

An Odoo API key is **not** the OAuth client secret. The client secret identifies
this ChatGPT connection; the interactive Odoo login chooses the user whose
permissions will be applied. Existing API-key integrations continue to work.

Administrators can revoke an individual consent at **Settings → MCP → OAuth
Connections**, or disable the client to block all its tokens. Rotating the client
secret revokes its existing consents, so update the secret in ChatGPT and reconnect.

The OAuth discovery documents are public, contain no credentials or business data,
and use addon-specific paths to avoid replacing another addon's OAuth endpoints:

- `/.well-known/oauth-protected-resource/odoo_mcp/mcp`
- `/.well-known/oauth-authorization-server/odoo_mcp/oauth`

Access tokens last **15 minutes**. With `offline_access`, single-use refresh tokens
rotate on each refresh and expire with consent after **30 days**. Reusing a consumed
code or refresh token revokes its entire consent. Clients must serialize refreshes;
a lost token response can require reconnecting. Codes expire after five minutes.
Tokens are bound to the MCP URL, OAuth issuer, client, user, consented company and
scopes. They are not Odoo API keys and cannot authenticate to Odoo's general RPC API.
Removing user permissions or disabling a user/client also limits or blocks access.

This is a narrowly scoped, built-in OAuth authorization flow for pre-registered
clients; it is not a general identity provider. It supports `client_secret_post`
and `client_secret_basic`, PKCE S256, issuer identification and revocation. It does
not offer DCR, CIMD, OIDC identity claims, client-credentials grants, or tokens in URLs.
Thus OIDC-dependent enterprise verified-domain policies are not supported.


Example initialization (replace placeholders locally; do not commit a key):

```bash
curl 'https://YOUR-ODOO-HOST/odoo_mcp/mcp' \
  -H "Authorization: Bearer $ODOO_MCP_API_KEY" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  --data '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"smoke-test","version":"1"}}}'
```

Then call `tools/list` and `tools/call`. Use `odoo_catalog` to see the precise field
allowlists. Example tool call:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "odoo_search",
    "arguments": {
      "resource": "lots",
      "filters": [["product_id", "=", 123]],
      "fields": ["id", "name", "product_id", "product_qty"],
      "limit": 50,
      "offset": 0
    }
  }
}
```

Filters are ANDed, use only listed fields and scalar comparison operators, and
cannot traverse related records. Search results are ordered by ID, at most 100
per page, with offsets bounded at 10,000. For larger scans, use `id > last_id`.
Lots' `product_qty` is aggregate stock; query `inventory` with lot/location IDs
for actual per-location quantities and reserved stock. Messages page newest first
using `before_id`. Message bodies and record descriptions are untrusted content.

## Security and operational behavior

- Each MCP request authenticates an Odoo API key or scoped OAuth token and executes with that user's ACLs
  and record rules. MCP also requires its own groups and rejects superuser mode.
- API-key requests use the user's default company. OAuth requests use the company
  shown on the consent screen, and fail if the user loses access to it. Shared
  records remain subject to Odoo rules. Clients cannot supply an arbitrary company/context.
- **The read-only guarantee applies to this MCP endpoint.** An Odoo API key also
  works with Odoo's normal APIs and inherits the user's application permissions.
  Use a dedicated user with minimal Odoo rights; this addon does not restrict
  other API endpoints or other installed modules/automated actions.
- OAuth credential storage and canonical configuration reads use narrowly scoped
  private `sudo()` helpers; business operations never elevate privileges. Browser Origin headers must
  match that configured URL. Server clients may omit Origin. No permissive CORS.
- Stateless JSON responses support MCP versions 2025-03-26, 2025-06-18 and
  2025-11-25. Unsupported incoming protocol headers are rejected. GET and DELETE
  return 405. Notifications return 202 and never run tools. Batches are rejected.
- Request bodies are capped at 128 KiB. Failed tools roll back their savepoint.
  Mutation logs include user/company/tool/record IDs, without payloads or tokens.
- Chat sends can notify channel members or CRM followers. CRM changes may trigger
  existing Odoo automated actions. Review those separately in your installation.
- Creation (including To-dos), messages and activity operations are **not retry-idempotent**. After
  a timeout, inspect the records before retrying; no exactly-once guarantee is made.
- There is no built-in rate limiter. Apply deployment-level request limits when
  exposing the endpoint publicly. External files/attachments are not exposed.

## Validation

Fast policy tests:

```bash
python3 -m unittest discover -s tests -v
```

GitHub Actions installs the addon in a clean Odoo 18 + PostgreSQL database and
runs its ORM and HTTP integration tests (access boundaries, scopes, field
compatibility, CRM/To-do lifecycles, chat membership, authentication and MCP requests).
OAuth tests cover discovery, consent/CSRF, PKCE, callback/resource/client binding,
scopes, expiration, revocation, refresh/code replay, credential storage permissions,
and preventing use of MCP OAuth tokens as general Odoo API keys.
The same tests can run against an isolated local Odoo database:

```bash
odoo -d mcp_test -i odoo_mcp --without-demo=all \
  --test-enable --test-tags /odoo_mcp --stop-after-init
```

Never run installation tests against a production database. Test with a staging
copy before production to check custom record rules, automated actions and other
addons in that particular installation.

## Existing alternatives

- [OCA REST framework / FastAPI (18.0)](https://github.com/OCA/rest-framework/tree/18.0)
  provides REST API infrastructure. It is useful for broader custom API projects,
  but does not by itself supply this MCP tool catalog and access policy.
- [oconsole/odoo-mcp-server](https://github.com/oconsole/odoo-mcp-server)
  is an existing external MCP server supporting Odoo 18. A separate service is a
  reasonable choice when its broader capabilities and deployment model fit.

This repository favors a small native addon and a fixed permission boundary
for Git-based Odoo hosting. It is not affiliated with Odoo or OCA.

License: LGPL-3.0-or-later.

Upgrading from 18.0.1.0.0: pull branch `18.0`, upgrade **Odoo MCP Gateway** in Apps,
then assign **MCP: To-do write access** to the integration user. Existing CRM/chat
group assignments remain valid. To-do deadlines use `YYYY-MM-DD HH:MM:SS` in UTC
(or `false` to clear); priority is `0` (normal) or `1` (important).
