# Multi-tenancy

One WorkMind installation can serve several organizations (tenants) from the same database,
search index, cache and workers. This page says how each layer keeps tenants apart, how a
sign-in finds its tenant, how to create and completely delete a tenant, which abuse limits
apply per person and per tenant, how isolation is tested, and what a hosted multi-tenant
service would still need.

Code: `core/tenancy.py` (database transactions, Valkey key names), `modules/retrieval/qdrant.py`
(tenant scope in every search-index call), `modules/knowledge/storage.py` (file keys),
`modules/identity/lifecycle.py` and `tenant_cli.py` (`wm tenant`), `core/ratelimit.py` (limits).
Tests: `apps/api/tests/integration/test_cross_tenant.py`, `test_tenant_lifecycle.py`,
`test_rate_limits.py`, `test_tenant_isolation.py`.

## How isolation works, layer by layer

| Layer | What keeps tenants apart | Checked by |
|---|---|---|
| PostgreSQL | Every tenant table has a `tenant_id` column, Row-Level Security that is **forced** (it applies to the table owner too) and a `tenant_isolation` policy. Each transaction sets `app.tenant_id`; without it no row matches. The runtime role has no `BYPASSRLS` and cannot write the `tenants` table. Links between rows use composite `(tenant_id, id)` foreign keys, so a row can never point at another tenant's row, even with a guessed id. | `test_tenant_isolation.py` (guardrail over every table, reads, writes, moves, references) |
| Qdrant | One collection per embedding model, shared by all tenants, with `tenant_id` indexed as the tenant key. `QdrantClient` itself adds `tenant_id = <tenant>` to the `must` part of every query, count, delete and payload update, and refuses to write a point whose payload names another tenant. A caller cannot leave the condition out, and nothing it adds (`should`, `must_not`, another tenant's id) can widen it. Searches then also apply the person's ACL (lock 1) and recheck OpenFGA (lock 2). | `test_cross_tenant.py`: crafted filters, a context that carries another tenant's principals, deletes and relabels aimed at another tenant's document |
| OpenFGA | One store per tenant (`tenants.openfga_store_id`, named `tenant-<slug>`). Every check goes to the asking person's tenant's store. | `test_cross_tenant.py`: checks for tenant A's collection and document in B's store, `list-objects` in B's store, B's tuples |
| Object storage | Files live at `tenants/<tenant_id>/objects/<sha256>`. Keys are built only from a SHA-256 of the content, never from a file name. Every key read from the database goes through `owned_key(tenant, key)` before it is read or deleted, so even a wrong row cannot serve another tenant's file. The local implementation refuses any key that resolves outside its root. | `test_cross_tenant.py`: paths of every stored file, traversal keys, a B row pointing at A's file (refused with a 500, no byte served) |
| Analytics files | Each tenant's datasets are DuckDB files at `<storage>/analytics/<tenant_id>/<version>.duckdb`; versions match a fixed pattern, table names a strict identifier pattern. Queries run read-only, without file access, in worker processes. | `test_cross_tenant.py`: A's table queried by B, `read_csv` of A's directory, `../` table names |
| Valkey | Every key a tenant owns starts with `t:<tenant_id>:` (run events, the one-answer-at-a-time lock, cached usage sums, budget alert flags, OAuth flows, the tenant's session index (pruned to one session lifetime at each sign-in), rate-limit counters, each person's running analytics queries). A run is looked up under the asker's tenant, so another tenant's run id finds nothing. The only other keys are listed in `core/tenancy.py` (`GLOBAL_KEY_PREFIXES`): session data by the SHA-256 of the cookie (it names the tenant inside), Keycloak session indexes for back-channel logout, unfinished sign-ins, and sign-in rate limits per address. | `test_cross_tenant.py`: a scan of every key written while two tenants were used |
| Celery | Every task about one tenant's data takes the tenant id as its first argument and works inside that tenant. A missing or malformed tenant id raises `TenantRequiredError` (never retried); a suspended or deleted tenant's task does nothing. Another tenant's record id finds nothing. | `test_cross_tenant.py`: tasks without a tenant, with B's tenant and A's ids, with an unknown tenant |
| Sessions and tokens | A session names its tenant (set at sign-in); the user and the tenant's status are loaded on every request, so disabling a person or suspending a tenant applies at once. A personal access token names its tenant's slug and is only looked up there. | `test_tenant_lifecycle.py` (suspension), `test_cross_tenant.py` (a token with another tenant's slug) |
| Models and keys | Model providers, catalog, roles and policy are per tenant; provider API keys are encrypted at rest (one deployment key, `SECRETS_ENCRYPTION_KEY`). | `test_cross_tenant.py`: no key or provider of A in any of B's responses or prompts |
| Logs | From the moment a request is authenticated, its log lines (and its access log line) carry `tenant_id`. Background tasks and traces do not carry it yet. Content is not logged. | `test_tenant_lifecycle.py` (access log) |

Permissions are always decided in code, never by a model: a prompt can only contain what the
asking person may read in their own tenant.

## How a sign-in finds its tenant

WorkMind uses one Keycloak realm. Each tenant is a **Keycloak organization whose alias is the
tenant's slug**; Keycloak adds the person's organizations to the ID token (`organization`
claim). At `/api/auth/callback` WorkMind takes the first **active** tenant whose slug is one of
those aliases (`resolve_tenant`), then links the person (by Keycloak subject, or by email if
Keycloak says the email is verified) or creates them just in time. A person whose organization
matches no active tenant is refused (`no_organization`). The session stores the tenant, and
every later request uses it; the browser never chooses a tenant.

Access tokens for MCP clients and scripts are `wm_pat_<slug>_<secret>`: the slug says which
tenant to look in, and only the SHA-256 of the whole token is stored there. Changing the slug
sends the token to a tenant that has no such hash (401).

Someone in two organizations signs in to the first matching tenant by slug order. Choosing
between tenants at sign-in is not built.

## Running several tenants

1. Create the tenant (as an operator, with the owner database role):

   ```bash
   wm tenant create globex --name "Globex" --owner-email it@globex.example \
     --keycloak --domain globex.example
   ```

   This creates the tenant row (with `restricted_data: local_only`), its OpenFGA store with the
   current model, the first owner (invited: linked at their first sign-in with that verified
   email), an audit event `tenant.created` and, with `--keycloak`, the Keycloak organization.
   Without `--keycloak`, create the organization yourself with alias = slug. With
   `--keycloak`, the command refuses a slug whose organization still exists in Keycloak (for
   example one left by a deletion without `--keycloak`): its members would sign in to the new
   tenant. Delete that organization in Keycloak first, or choose another slug.
2. In Keycloak, add people to the organization or link the company's identity provider to it.
3. The owner signs in and sets up departments, groups, collections and models in Admin.

`wm tenant list` shows every tenant with its id, status and number of people. The demo seed
(`wm seed`) creates the Acme tenant the same way, with its demo logins.

## Deleting a tenant completely

```bash
wm tenant delete globex --confirm globex [--keycloak]
```

The steps, in an order that can be run again after a failure:

1. **Suspend** the tenant: from now on its sign-ins, sessions and access tokens are refused and
   its background tasks do nothing.
2. **End its sessions** in Valkey (the tenant's session index, then a scan for older ones).
3. **Search index:** delete its points from every Qdrant collection (one per embedding model).
4. **Files:** delete `tenants/<id>/` (originals, recordings) and `analytics/<id>/` (datasets).
5. **Authorization:** delete every tuple in its OpenFGA store, then the store.
6. **Cache:** delete every Valkey key under `t:<id>:`.
7. **Keycloak** (with `--keycloak`): delete the organization and the Keycloak users that
   belonged to no other organization (people in several organizations are shared with other
   tenants and stay).
8. **PostgreSQL:** delete the `tenants` row; every tenant table follows by `ON DELETE CASCADE`,
   the audit log and usage history included.

Then it **verifies** that nothing remains and exits with 1 if anything does, listing it:
no `tenants` row; no row with the tenant's id in any table that has a `tenant_id` column (the
same list as the RLS guardrail, counted inside the tenant because forced RLS applies to the
owner role too); no point in any Qdrant collection; no file under its prefixes; no Valkey key
under its prefix and no session naming it; the OpenFGA store gone (and no other live store named
`tenant-<slug>`); with `--keycloak`, no organization with its alias. To check again later:

```bash
wm tenant verify-empty <tenant-id> --slug globex --store <openfga-store-id> [--keycloak]
```

`test_tenant_lifecycle.py` creates a tenant with `wm tenant create`, signs its owner in, fills
every layer (a collection and a document: rows, points and a file; a conversation with an
answer: run events and usage; an analytics table: a DuckDB file), checks that `verify-empty`
sees all of them, deletes it, and checks that `verify-empty` then finds nothing, that its
sessions are refused, and that another tenant's rows, search and session are unchanged.

What deletion does **not** reach:

- **Backups** of PostgreSQL, Qdrant, Valkey or the storage directory taken before the deletion.
- **OpenFGA's own record of the store:** OpenFGA marks a deleted store instead of erasing it.
  WorkMind deletes every tuple first, so what is left is the store id, its name
  (`tenant-<slug>`), timestamps and the generic authorization model.
- **Logs and traces** already shipped elsewhere (they hold ids, not content).
- **Keycloak** without `--keycloak`, and Keycloak users shared with other organizations.
- **Queued Celery messages** that name the tenant: they still exist in the broker until a
  worker takes them, and then do nothing (the tenant is gone).
- Work already running when the tenant was suspended (a sync or an ingestion in progress) can
  write after the step that cleaned its layer; verification then reports it, and running
  `wm tenant delete` again with the tenant's id removes it (the row is the last thing deleted).

## Abuse limits

Expensive endpoints count requests per person and per tenant in fixed one-minute windows in
Valkey. A person over their own limit is refused without using up their tenant's allowance; a
tenant over its limit is refused for everyone in it; other tenants never notice, because every
counter is under its tenant's key prefix. Sign-in is counted per client address.

| Limit | Endpoints | Per person | Per tenant |
|---|---|---|---|
| `chat` | a message in a conversation, `POST /ask`, a request (`POST /requests`, `POST /admin/requests/inbound-email`) | 30 | 600 |
| `research` | `POST /research`, a message in research mode | 6 | 120 |
| `search` | `POST /search` | 120 | 2400 |
| `agent_test` | trying an agent draft, testing a model provider, running a workflow now, previewing request routing, testing Slack | 10 | 100 |
| `analytics` | `POST /analytics/ask`, `POST /analytics/query` | 20 | 400 |
| `ingest` | document uploads and re-indexing, meeting uploads, analytics table uploads | 60 | 600 |
| `sync` | `POST /admin/sources/{key}/sync` | 6 | 30 |
| `mcp` | every request to `/mcp`, per token owner | 120 | 1200 |
| `login` | `GET /api/auth/login`, `/api/auth/callback`, per client address | 60 per address | |

A refusal is `429` with `Retry-After` (seconds until the window ends) and a problem document:

```json
{"type": "about:blank", "title": "Too many requests", "status": 429, "code": "rate_limited",
 "detail": "At most 30 questions per minute per person. Try again in 41 s.",
 "limit": {"bucket": "chat", "scope": "user", "limit": 30, "window_s": 60, "retry_after_s": 41}}
```

`scope` is `user`, `tenant` or `address`. Change limits with `RATE_LIMITS` (JSON), e.g.
`RATE_LIMITS='{"chat": {"per_user": 10}, "mcp": {"per_tenant": 0}}'` (0 removes that limit;
an unknown limit name or field stops startup), or turn them off with `RATE_LIMITS_ENABLED=false`.
Limits are the same for every tenant; per-tenant limits are not built.

Things to know:

- **Fixed windows** let up to twice a limit through around a window boundary. Budgets
  ([usage.md](usage.md)) bound what model calls can cost; per-person analytics concurrency
  (`ANALYTICS_QUERIES_PER_USER`) bounds parallel queries.
- **If Valkey is down** requests are let through (and a warning logged). Sessions live in
  Valkey too, so sign-in and every authenticated request fail in that case anyway.
- **Client address:** behind Caddy, uvicorn takes it from `X-Forwarded-For`
  (`FORWARDED_ALLOW_IPS="*"` in compose; Caddy sets the header itself). Anyone who can reach the
  API port directly can send their own `X-Forwarded-For`; compose binds that port to
  `127.0.0.1`. An office behind one address shares its sign-in limit.
- Keycloak's own brute-force protection (10 failures) applies to passwords.

## How isolation is tested

`apps/api/tests/integration/test_cross_tenant.py` seeds two tenants, A and B, each a full copy
of Acme. A gets data in every module (a document with points in Qdrant, a conversation with an
answer, an agent with two versions, a model provider with an API key and a catalog model, usage
and a budget, a workflow and a run, an approval, a task, notifications, a meeting with notes,
an analytics table and a run, a review, audit events, two connectors (one with a token) and a
started Google OAuth flow, an access token), every piece carrying a canary phrase. Then B's org
owner:

- calls **every route of the app**, enumerated from `app.routes` with the Enterprise routes
  loaded: 142 method-and-path pairs, 160 requests, with A's ids in the path, the query and the
  body. Each route has an entry in `PROBES`; **a new route fails
  `test_every_route_is_classified` until it gets one**. Requests that address A's records must
  get 403 or 404; requests that make B's own records point at A's (a grant to A's people, a
  task assigned to A's user, a budget for A's department) must be refused. No response may
  contain a canary, a secret of A, A's slug or an id of A that B did not send itself, and no
  prompt sent to the model may contain A's data. Afterwards A's rows (a digest of every
  tenant table), Qdrant points, files and Valkey keys must be exactly as before.
  `test_the_probes_address_real_data_of_a` sends the same reads as A's own people and expects
  200, so a probe cannot pass by pointing at nothing;
- queries Qdrant and the retriever with crafted filters, through MCP, and with a forged token;
- checks every Valkey key written, the OpenFGA stores, the Celery tasks and the storage paths.

What it found and what changed:

- Collections accepted viewers and editors naming another tenant's people, groups, departments
  or organization (stored as grants, written to B's OpenFGA store and Qdrant ACLs; not readable
  by A's people, but a dangling cross-tenant reference). Now 422 `unknown_principal`.
- A department collection with another tenant's department answered 500 (the composite
  foreign key refused it). Now 422 `unknown_department`.
- Run events, the conversation lock, usage caches and budget alert flags were stored under
  keys without the tenant (`wm:run:<id>`, `wm:usage:...`); an OAuth callback looked up any
  tenant's flow and deleted it before checking the tenant. All now live under `t:<tenant_id>:`.
- The tenant condition for Qdrant was added by callers; one delete had none. It is now added
  inside `QdrantClient`.
- A suspended tenant's existing sessions kept working. They are now refused.

Run them like the other integration tests (they need PostgreSQL, Valkey, Qdrant and OpenFGA):
`make test`, or `uv run pytest tests/integration/test_cross_tenant.py` with the `TEST_*`
variables set. On the development machine the cross-tenant module took between 10 and 100 s
per run (the slow runs shared the infrastructure with other test runs).

Not covered by these tests: the web app (it only calls these routes), Keycloak itself (a
scripted Keycloak API tests the deletion logic; like Keycloak 26, its `search` parameter matches
organization names and domains, never aliases, so the lookup by alias pages through every
organization), and load (two tenants, one process).

## What a hosted multi-tenant WorkMind Cloud would still need

The isolation above is what a self-hosted installation with several tenants needs. A hosted
service run for many unrelated customers would also need, at least:

- **Self-service sign-up and billing:** tenants are created by an operator with `wm tenant`.
- **Per-tenant encryption keys** (envelope encryption with a KMS). Today one deployment key
  encrypts every tenant's connector credentials and one encrypts model provider keys.
- **Fair sharing of workers:** all tenants share the Celery queues and the analytics worker
  pool; a tenant's bulk import or heavy queries can delay others (rate limits and per-person
  analytics concurrency soften this; per-tenant queues or quotas are not built).
- **Quotas** on stored documents, files and index size per tenant, and per-tenant rate limits.
- **S3-compatible object storage** (only a local directory is built) and per-tenant buckets or
  keys if required.
- **Dedicated Qdrant collections or databases** for large tenants (ARCHITECTURE section 8
  mentions this; not built), and a database-per-tenant option for customers who require it.
- **Per-tenant backup, restore and export**, and deletion that reaches backups (crypto-shredding
  with per-tenant keys is the usual way).
- **Edge protection:** a WAF or DDoS protection in front, since these limits run in the app.
- **Operator access controls and audit** (who at the provider can read which tenant), data
  residency choices, and per-tenant session lifetimes (ARCHITECTURE 6.3 lists them; not built).
- **Choosing a tenant at sign-in** for people who belong to several.
