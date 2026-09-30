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
| CRM chatter | Read; post internal notes |
| To-do | Read personal tasks assigned to the authenticated user, with no project |
| Contacts, users, CRM stages/teams and activity types | Read selected reference fields |

Invoices and To-do default to read-only. There is no generic model access,
method execution, delete, SQL, arbitrary context, relation traversal, field
discovery, or write access to sales/stock/accounting. There are no sync hooks,
crons, manufacturing operations, or dependencies on other custom connectors.
CRM/contact references use existing IDs; the addon never creates contacts.
Chatter writes on sales, inventory, invoices and To-do are deliberately excluded.

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
   - **MCP: Chat write access** for Discuss messages and CRM notes.
5. Generate an Odoo API key for that user under its account security settings.
   Use a defined expiration and rotate/revoke keys through Odoo.
6. Connect a client supporting **Streamable HTTP with a custom Bearer header**:
   - URL: `https://YOUR-ODOO-HOST/odoo_mcp/mcp`
   - Header: `Authorization: Bearer YOUR-ODOO-API-KEY`
   - Header: `Accept: application/json, text/event-stream`

Cloudpepper must route the hostname to the correct database (`dbfilter` for
multi-database servers), preserve Authorization headers, and serve HTTPS.
Pushes make the addon available in GitHub; Cloudpepper still needs its configured
pull/rebuild and initial installation. An updated addon may require an Apps upgrade.

This server uses **preconfigured Odoo API keys**, not an OAuth authorization
server. Clients that require interactive OAuth and cannot supply a Bearer header
need an external authentication bridge; adding this URL alone will not provide OAuth.
Legacy HTTP+SSE and stdio transports are not provided.

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

- Each request authenticates an Odoo API key and executes with that user's ACLs
  and record rules. MCP also requires its own groups and rejects superuser mode.
- HTTP requests use the user's default company, including shared records allowed
  by Odoo. Change the integration user's default company or use separate users
  for different companies; clients cannot supply an arbitrary company/context.
- **The read-only guarantee applies to this MCP endpoint.** An Odoo API key also
  works with Odoo's normal APIs and inherits the user's application permissions.
  Use a dedicated user with minimal Odoo rights; this addon does not restrict
  other API endpoints or other installed modules/automated actions.
- The only `sudo()` call reads the canonical `web.base.url` for Origin validation;
  business operations never elevate privileges. Browser Origin headers must
  match that configured URL. Server clients may omit Origin. No permissive CORS.
- Stateless JSON responses support MCP versions 2025-03-26, 2025-06-18 and
  2025-11-25. Unsupported incoming protocol headers are rejected. GET and DELETE
  return 405. Notifications return 202 and never run tools. Batches are rejected.
- Request bodies are capped at 128 KiB. Failed tools roll back their savepoint.
  Mutation logs include user/company/tool/record IDs, without payloads or tokens.
- Chat sends can notify channel members or CRM followers. CRM changes may trigger
  existing Odoo automated actions. Review those separately in your installation.
- Creation, messages and activity operations are **not retry-idempotent**. After
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
compatibility, CRM lifecycle, chat membership, authentication and MCP requests).
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
