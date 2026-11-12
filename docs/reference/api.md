# HTTP API

Everything the web app does goes through this API, under `/api/v1` on the same origin as the
app (`http://localhost:8480/api/v1/...` in development). The reference below is generated from
the code: `make docs-gen` exports the OpenAPI schema to
[`openapi.json`](openapi.json), and a unit test fails when the committed schema is out of date.

On a running development stack, http://localhost:8480/api/docs serves the same schema with
requests you can send. It is off when `APP_ENV=production`.

## Authentication

| | How | Can |
|---|---|---|
| **Browser session** | Sign in at `/api/auth/login` (Keycloak, OpenID Connect). The API keeps the session and sets an `httpOnly` cookie; the browser never holds a token ([ADR 0003](../adr/0003-bff-authentication-with-keycloak.md)). | Everything the person may do in the app |
| **Personal access token** | `Authorization: Bearer wm_pat_...`, created in **Settings › Access tokens**, with scopes `knowledge:read` and `assistant:ask` | Search, list collections, ask: as the person, never with more access. Administration, uploads and approving official status need a browser session. |

A request with a session cookie that changes something (`POST`, `PUT`, `PATCH`, `DELETE`) must
come from the app's own origin and carry the header `X-CSRF-Protection: 1`; otherwise it is
refused with `403`. Bearer tokens are not sent by browsers on their own, so this check does not
apply to them.

## Conventions

- **Errors** are [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457) problem details
  (`application/problem+json`) with a stable `code`, for example `not_found`, `scope_required`,
  `budget_exceeded` (429) or `enterprise_feature` (402).
- **Not yours looks like not there.** A resource you may not read answers `404`, the same as one
  that does not exist, so the API does not reveal that it exists.
- **Paging:** conversations, people (admin) and the audit log take a `cursor` (the previous
  page's `next_cursor`) and a `limit`.

## Streaming answers

Asking in a conversation is two calls:

1. `POST /api/v1/conversations/{id}/messages` with `{"content": "...", "mode": "answer"}` (or
   `"research"`) returns `202` with a `run_id`.
2. `GET /api/v1/runs/{run_id}/events` streams the run as server-sent events. A dropped connection
   resumes with the `Last-Event-ID` header; closing the tab does not stop the run.

| Event | Data |
|---|---|
| `run.started` | The run and its conversation |
| `query.rewritten` | The standalone question that was searched |
| `retrieval` | The sources found (titles, sections, trust, sensitivity) |
| `agent.step` | A department agent's progress (agents mode) |
| `tool.step`, `approval.required` | Research mode: a tool's result, an action waiting for approval |
| `token` | The next piece of the answer's text |
| `citation` | A marker in the answer and the source it points to |
| `verification` | Markers that point to no source given to the model |
| `run.completed`, `run.failed`, `run.cancelled` | The end of the run: the stored message, or the error |

`POST /api/v1/runs/{run_id}/cancel` stops a run. `POST /api/v1/ask` answers in one response
instead, for scripts.

## Not in this schema

- **Enterprise routes**, which exist only with a license: retention policies
  (`/api/v1/admin/retention...`) and the SIEM export of the audit chain
  (`/api/v1/admin/audit-events/export.jsonl`). Without a license they answer `402`
  ([Enterprise edition](../enterprise.md)).
- **The MCP server** at `/mcp` (Streamable HTTP, bearer token): see [MCP](../mcp.md).
- **Sign-in** (`/api/auth/login`, `/api/auth/callback`, `/api/auth/logout`) and the probes
  (`/healthz`, `/readyz`) are in the schema; the probes need no sign-in.

## Reference

<swagger-ui src="openapi.json"/>
