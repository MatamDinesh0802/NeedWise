# Changelog

Notable changes to WorkMind AI. Versions follow [Semantic Versioning](https://semver.org); before
1.0, a minor version is a phase of the [roadmap](docs/ROADMAP.md). From the first release made
with release-please on, entries are generated from Conventional Commits and edited in the release
PR ([docs/operations/releasing.md](docs/operations/releasing.md)).

**About v0.2 to v0.11:** only v0.1.0 was tagged. Phases 2 to 11 were finished on `main` without a
tag, a GitHub Release or published images, and the package files still say 0.0.1. Their sections
were written afterwards from the git history and the roadmap; the numbers in them come from the
committed evaluation reports in [evals/results](evals/results).

## Unreleased: Phase 12, production hardening (v1.0)

Supply chain and releases ([docs/security/supply-chain.md](docs/security/supply-chain.md)):

- A Security workflow on pushes, pull requests and weekly: osv-scanner on `uv.lock` and
  `pnpm-lock.yaml`, Trivy on the repository (vulnerabilities, misconfigurations, secrets) and on
  the built api and web images, hadolint, actionlint and zizmor; SPDX SBOMs kept as artifacts.
  CodeQL for Python, TypeScript and the workflows. Dependabot for uv, npm, Dockerfiles, Compose
  and actions: weekly, grouped, with a 7-day cooldown. The same scans run locally with
  `make scan` and `make scan-images`.
- Images: base images pinned by digest, OS security updates applied at build time, no pip, npm,
  corepack or yarn in the runtime images, numeric non-root users. Fixable HIGH findings went from
  7 to 0 in the api image and from 6 to 0 in the web image, which now has no known vulnerability
  at any severity.
- Every third-party image in `compose.yaml` and CI is pinned by digest; the app images can be
  replaced with released ones (`WORKMIND_API_IMAGE`, `WORKMIND_WEB_IMAGE`); third-party actions are
  pinned to commit SHAs; the gitleaks download in CI is checked against its SHA-256.
- Releases: release-please, and a release workflow that builds linux/amd64 and linux/arm64 images
  for GHCR with SBOM and provenance attestations, scans both platforms, runs the seeded stack from
  those images through the permission gate, and only then tags them
  ([docs/operations/releasing.md](docs/operations/releasing.md)).

Security review ([docs/security/owasp-llm-top10.md](docs/security/owasp-llm-top10.md),
[threat model](docs/security/threat-model.md)):

- Eleven fixes, each with a regression test: the web crawler follows redirects only within its
  site and checks every host against an outbound URL policy (SSRF); stored answers, research text
  and MCP `ask` results carry no images; citation markers inside sources and tool results reach
  the model neutralized; tool results, transcripts, workflow event fields and the conversation
  summary are escaped or quoted as data; guests cannot send email or create tasks for others; at
  most `MAX_INFLIGHT_PER_USER` (4) answers in progress per person; at most 12 tool calls per
  research run; Slack syncs are always `discussion`; links in answers show their host.
- The red team grows from 16 to 59 attacks on four surfaces (chat, agents, research, analytics),
  scored for leaks (the gate) and violations (reported); three injection documents join the seed
  corpus (47 documents).

Multi-tenant hardening ([docs/multi-tenancy.md](docs/multi-tenancy.md)):

- Every tenant's Valkey keys under `t:<tenant_id>:`; the tenant condition inside the Qdrant
  client; suspended tenants' sessions end; Celery tasks refuse a missing tenant; stored file keys
  checked against the reading tenant; grants to another tenant's people refused (422
  `unknown_principal`).
- `wm tenant list|create|delete|verify-empty`.
- Request rate limits per person, per tenant and per sign-in address (429 `rate_limited`;
  `RATE_LIMITS`, `RATE_LIMITS_ENABLED`).
- A cross-tenant suite that calls every route with another tenant's ids.

Connectors, the rest of Phase 7 ([docs/connectors.md](docs/connectors.md)):

- A source health dashboard in Admin › Sources, and Knowledge Hub › Sources for the owners of the
  collections sources sync into: health (healthy, failing, paused, never synced), the mapping and
  who can see it, last successful and next scheduled sync, what the last run changed, errors in
  plain words, a run history per source, pause and resume, Disconnect after typing the key.
- Stale synced documents: unchanged in the source past the source's threshold, or missing
  upstream (a cut-short scan no longer lists them). A daily task reminds their owners once per
  staleness episode; a source that fails `SOURCE_FAILURE_ALERT_AFTER` syncs in a row (default 3)
  notifies the people who manage it once per failure episode, and again when it recovers.
- Connect Slack with OAuth (`SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET`); a pasted bot token still
  works.
- Confluence Cloud sources (REST API v2): the current pages of one or more spaces as Markdown
  converted from the storage format, their readable attachments, the page tree as source
  metadata, links back; signs in with an API token or OAuth 2.0 (3LO). SharePoint and OneDrive
  sources (Microsoft Graph): a site's library, a drive or the connecting account's OneDrive,
  optionally one folder; delta queries with the delta link stored, tombstones as deletions, a
  full listing when a folder leaves or enters the selection; OAuth with PKCE and the least Graph
  permission for the selection; 429 Retry-After honoured. Both are tested against fakes built
  from the API documentation, not against a live site or tenant.
- The sync engine applies deletions the source reports (delta tombstones), keeps the cursor when
  a download failed, keeps each document's place in its source (`documents.source_meta`), and
  stores renewed OAuth tokens also after a failed sync.
- `GET /api/v1/admin/sources/kinds` describes every connector (label, how it signs in, fields,
  configuration schema). New settings: `ATLASSIAN_CLIENT_ID`, `ATLASSIAN_CLIENT_SECRET`,
  `MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET`, `MICROSOFT_TENANT_ID` (Helm:
  `config.atlassian`, `config.microsoft` and the matching credentials).

Operations: `make backup` and `make restore` with a measured restore drill
([backup and restore](docs/operations/backup-restore.md)); an upgrade test from a release tag,
passed from v0.1.0 ([upgrades](docs/operations/upgrade.md)); `wm authz sync`.

Deployment and docs: a Helm chart in `deploy/helm/workmind`, validated, and installed, tested,
upgraded and uninstalled on kind clusters with Kubernetes 1.34 and 1.37 (`make helm-kind-test`;
[docs/deploy/kubernetes.md](docs/deploy/kubernetes.md#tested-on-a-cluster)); the docs site (`make docs`,
`make docs-serve`) with the configuration and HTTP API reference generated from the code.

Tests: a browser end-to-end suite in `e2e/browser` (sign-in per role, every page, permissions,
axe accessibility checks, phone layout), run in CI, and the UI fixes it found; a Locust load-test
harness with a stub model server ([docs/load-testing.md](docs/load-testing.md)).

Upgrade notes so far:

- The next `make up` pulls the digest-pinned images. `postgres:18.6` resolves to a newer build of
  the same tag than older checkouts pulled; the data volume is unaffected.
- The api image runs as `10001:10001` (the user ID is unchanged; the group is now 10001) and the
  web image as `1001:1001` (unchanged). Existing volumes keep working.
- Valkey key names changed (run event streams, usage caches, Google OAuth flows moved under
  `t:<tenant_id>:`): an answer streaming during the upgrade loses its event stream, usage caches
  rebuild within 60 s, and a Google Drive connection started in the last 10 minutes must be
  started again.
- New limits apply: 429 `rate_limited` above the request rate limits and 429
  `too_many_requests` above `MAX_INFLIGHT_PER_USER`. Scripts and load tests that share one login
  may need `RATE_LIMITS` or `RATE_LIMITS_ENABLED=false`.
- Behaviour changes: guests can no longer send email or create tasks for others; Slack sources
  sync as `discussion` whatever the collection's default trust; the crawler no longer follows
  redirects to other sites.
- Re-seed demo deployments (`make seed`): three new documents and a renamed sheet in the
  signature authority workbook.
- After an upgrade or a restore, `wm authz sync` brings each tenant's OpenFGA store to the
  current model.
- Migration `0020` adds the source run history and staleness columns. Existing synced documents
  start their staleness clock at their current version's date, so the first daily run after the
  upgrade may remind owners of documents unchanged for more than 180 days (Slack sources have no
  threshold). Collection owners who are not admins can now manage their collections' sources.
- Migration 0021 adds `documents.source_meta` and the `confluence` and `sharepoint` source
  kinds. A downgrade below it deletes Confluence and SharePoint sources (their documents stay,
  as uploads).

Automation: what Phase 8 left out ([docs/automation.md](docs/automation.md)):

- Approver rules: org admins, a department's or the requester's managers, any-of groups and
  sequential chains (edits carry to the next step; never the requester), in workflow steps and
  per tool for the assistant's actions (Admin › Automation).
- Reminders before an approval expires and escalation after the organization's SLA (backup
  approver, then managers, then org admins), once per approval step; due-date reminders and
  escalations for tasks. Hourly beat task `workmind.escalate_work`. Expiry stays the last step.
- Notifications by email (right away or a daily digest) and Slack (bot DMs, a channel webhook),
  per person and category, from an outbox sent and retried by `workmind.deliver_notifications`
  (every minute). Slack secrets are sealed and rotate with `wm secrets rotate`. Mentions in task
  descriptions notify colleagues who can open the task.
- Request routing: the Request form, an email-to-request endpoint for a mail bridge, and
  `create_task` without a queue are routed by admin rules, then the router model above a
  threshold, else to a triage queue; each decision is explained and audited. Usage feature
  `routing`. A 39-item eval set and the `request_routing` suite (not run with a real model yet).
- Workflows in YAML (comments kept) with problems at their lines, in the API and a
  line-numbered editor; events `document.updated`, `meeting.processed`, `task.completed` and
  `approval.decided`, exactly once per event and only for what the owner can open.
- Migration `0023`; new settings `SLACK_API_URL` and `SLACK_WEBHOOK_PREFIX`. Behaviour change: a
  workflow step naming an approver who no longer exists now fails the step instead of falling
  back to the owner.


Document access lists and permission sync ([docs/permission-sync.md](docs/permission-sync.md),
[ADR 0012](docs/adr/0012-document-access-lists.md)):

- A document may have its own access list, which only narrows its collection's: enforced by
  both locks (the Qdrant filter and the OpenFGA recheck, which also applies the PostgreSQL list)
  and on every other path (Knowledge Hub, counts, governance, approvals, reminders, workflow
  events, the stale-document lists). Editors set one with `PUT /api/v1/documents/{id}/acl`.
  Community.
- Identity mapping that fails closed: an e-mail matches a person and "anyone" the organization;
  groups, domains and Slack or GitHub accounts grant nothing until an admin maps them (Admin ›
  Sources › Source accounts).
- Enterprise `permission_sync`: each synced Drive file, private Slack channel and private or
  internal GitHub repository gets its source's list during every sync; Drive revocations from
  the changes feed every minute. Tested against fakes of the three APIs, not live. Each source
  on the Sources page says whether permission sync applies to it.

Meetings ([docs/meetings.md](docs/meetings.md)): speaker diarization with sherpa-onnx
(pyannote segmentation and NeMo TitaNet-S as ONNX on the CPU, the optional `diarization` extra,
included in the API image; the 47 MB of models download on first use), naming speakers as
colleagues or guests, and a topic outline per meeting.

Insights ([docs/analytics-sql-sources.md](docs/analytics-sql-sources.md)): PostgreSQL and MySQL
sources as datasets of their own, read-only three ways, with row policies as CTE fences and
sealed passwords; the BI agent across datasets; the steps of an answer streamed live on
/insights. Tested on PostgreSQL 18.6 and MySQL 8.4.13 with scripted models.

Governance ([docs/governance.md](docs/governance.md), [docs/enterprise.md](docs/enterprise.md)):

- Review escalation: an official document whose review stays overdue is escalated to the
  collection's owner, then the org admins, then (if the collection says so) demoted.
- Legal holds (Enterprise `legal_hold` to place them; enforced by the core on every deletion
  path whatever the license): documents, collections, people, meetings and Insights questions.
  Placing or releasing one notifies the other owners, admins and auditors.
- Document retention per collection (Enterprise, part of `retention_policies`): delete or
  archive documents not updated for N days, with a preview; archived documents leave search
  until restored.

Notifications: two new categories, "Sources" (failing sources and their recovery) and "Legal
holds"; review escalations count as escalations and stale synced documents as document reviews,
so email and Slack follow each person's choice for them.

Newcomers: the README path from a fresh clone measured (about 3 min 50 s to the first cited
answer with an empty build cache), a demo video and GIF (`make demo-video`), and a first
question without a model now says so (`model_not_configured`).

More upgrade notes (2026-11-13 merges):

- Migrations `0020` to `0026` run in that order (`0019` before). `0022` adds document lists and
  identity mappings, `0024` meeting topics, `0025` SQL sources, `0026` review escalation, legal
  holds and document retention; every new tenant table has forced row-level security.
- The OpenFGA model changed (a document's viewers are its collection's viewers who are also on
  its list): run `wm authz sync` after deploying (the Helm `authz-sync` hook does; `make seed`
  reconciles the demo tenant).
- Rebuild the api, worker and beat images: they now install the `diarization` and `mysql`
  extras. Re-run `make ee-dev-license` so a development license includes `permission_sync` and
  `legal_hold`, then restart api, worker and beat.
- Re-seed demo deployments (`make seed`): two documents with their own lists (49 in all) and the
  request-routing rules.
- With the default 14-day grace period, the seeded Company Handbook's overdue official documents
  reach the org admins' escalation stage at the first daily run from 2026-11-17.

## v0.11 — Phase 11: admin, governance and usage analytics (2026-11-12, not tagged)

- **Open-core boundary** ([ADR 0011](docs/adr/0011-open-core-boundary-and-license-keys.md)):
  Ed25519-signed license keys decide the edition; an extension registry, `GET /edition` and
  `require_feature`; the `workmind-ee` package is built into the API image and stays inactive
  without a license; `wm license dev` for development licenses.
- **Agents as configuration** ([docs/agents.md](docs/agents.md)): tools, model, limits, numbered
  versions with restore, and a test console in Admin › Agents. A Legal agent created in the
  browser, with no code, routes 15/15 legal questions and 53/56 overall with Claude Sonnet 5.5.
- **Models per organization** ([docs/models.md](docs/models.md)): providers with encrypted,
  write-only keys, a catalog with prices, a model per role and per agent, a policy for sensitive
  data sent to external models, `wm secrets rotate`.
- **Usage and budgets** ([docs/usage.md](docs/usage.md)): every model call recorded (tokens and
  cost, never text) per person, department, agent and feature; budgets checked before model work
  and on every turn of a tool loop; the Usage page and Admin › Budgets. Enterprise: department
  budgets and the chargeback report.
- **Knowledge governance** ([docs/governance.md](docs/governance.md)): document owners and review
  reminders; "official" status needs a second person's approval, covers one version, and only
  people who can read the collection decide; the audit explorer with filters and CSV export.
  Enterprise: retention policies and the SIEM audit export.
- A Legal & Contracts collection (10 documents) and legal routing questions in the seed data.
- Fixed: beat keeps its schedule in a volume, so daily tasks survive restarts.

## v0.10 — Phase 10: business intelligence agent (2026-11-12, not tagged)

- CSV and Excel tables in a versioned DuckDB per tenant, described once in a semantic layer
  ([ADR 0010](docs/adr/0010-analytics-semantic-layer-duckdb-sqlglot.md)); a SQL guard with row
  policies; a sandboxed, read-only query engine with limits.
- The BI agent: a tool loop that queries as the person asking, a chart, the SQL, and every number
  in its narrative checked against the query results. The Insights page, Admin › Analytics, and
  the `query_data` and `describe_data` tools for research mode and MCP.
- A 51-question text-to-SQL evaluation. With Claude Sonnet 5.5: execution accuracy 33/36,
  0 permission leaks, 6.9 s median and $0.026 per answer
  ([report](evals/results/2026-11-12-analytics.md)).
- Fixed after review: row policies fenced, a recursive-CTE bypass closed and query limits
  enforced; answers never load images from their markdown, and production builds send a
  same-origin Content-Security-Policy.
- Not built: Postgres and MySQL sources ([docs/analytics.md](docs/analytics.md)).

## v0.9 — Phase 9: meeting intelligence (2026-11-04 to 2026-11-12, not tagged)

- Recordings and transcripts become a summary, decisions and action items that link to the
  moment they were said, one-click tasks, and indexed knowledge
  ([docs/meetings.md](docs/meetings.md)).
- Local speech-to-text with faster-whisper: a 23-minute meeting in under a minute on a laptop
  CPU. Colleagues' names are a hint to the model, and owners can be corrected.
- The Meetings pages, a recording player that seeks (HTTP Range), "My commitments", deleting a
  meeting with its file and transcript.

## v0.8 — Phase 8: workflow automation and approvals (2026-11-04 to 2026-11-12, not tagged)

- Approvals with people in control: an inbox and inline approval cards (approve, edit, reject);
  agent actions are stored proposals that a decision resumes.
- Tasks and team queues, notifications, a colleague directory; declarative workflows with
  manual, scheduled and event triggers (a ready document version starts the workflows listening
  for it); Admin › Workflows ([docs/automation.md](docs/automation.md)).
- Not built: escalation, email and Slack notifications, request routing.

## v0.7 — Phase 7: connectors and sync (2026-11-04, not tagged)

- Sources keep a collection in sync with another tool: scan and fetch connectors, scheduled
  incremental syncs run by beat, deletions applied only after a complete scan, encrypted
  credentials, Admin › Sources ([docs/connectors.md](docs/connectors.md)).
- Not verified: live sync against a real Google Drive and Slack workspace.

## v0.6 — Phase 6: tools and MCP (2026-11-04, not tagged)

- Governed tools with side-effect levels, permission checks and audit; a research agent;
  "Research & act" in chat.
- WorkMind as an MCP server at `/mcp`, authenticated with personal access tokens, so Claude Code,
  Cursor or Claude Desktop ask with the user's permissions ([docs/mcp.md](docs/mcp.md)).

## v0.5 — Phase 5: multi-agent orchestration (2026-11-04, not tagged)

- Department agents (HR, Engineering, Finance) with a planner and a fast path, in plain asyncio
  ([ADR 0009](docs/adr/0009-orchestration-without-a-graph-framework.md)); a live agent trace in
  chat; `ASSISTANT_MODE=single|auto|agents`.
- Measured on 15 cross-department questions: agents answered 15/15 with every required fact,
  the single pipeline 14/15, at 19.2 s against 6.6 s (p50) and $0.098 against $0.022 per answer.
  The single pipeline stays the default.

## v0.4 — Phase 4: the assistant (2026-11-04, not tagged)

- Conversations with streamed, replayable runs; chat with citations, a sources panel and the
  "view as" demo switcher.
- `make quickstart`: the stack, a local embedding model in Docker and the demo company.
- Claude Sonnet 5.5 is the default model: the same 81/82 correct answers as Opus 5.5, at 1.2 s to
  the first token (p50) and $0.0066 per answer, 40% of the cost.
- Not done: the public launch (there is no public repository yet).

## v0.3 — Phase 3: retrieval engine and evaluation (2026-11-04, not tagged)

- Hybrid retrieval (dense and BM25, fused with RRF k=2 and BM25 weight 0.5,
  [ADR 0008](docs/adr/0008-retrieval-fusion-and-sensitivity-routing.md)), the second permission
  lock (OpenFGA re-checks every source), citations and abstention.
- Sensitivity routing: restricted sources go only to a local model, or are left out
  (`restricted_data: local_only` by default).
- `wm eval`: retrieval QA, a red team with canary tokens, abstention. The permission gate runs in
  CI. With the default configuration (Claude Sonnet 5.5): 0 leaks, 81/82 correct answers, 99.4%
  faithfulness; evidence@1 92.7% with the default fusion against 69.5% for textbook RRF
  ([evals/README.md](evals/README.md)).

## v0.2 — Phase 2: knowledge ingestion (2026-11-04, not tagged)

- Collections with sharing; upload of PDF, Word, Excel, PowerPoint, Markdown, HTML, CSV and Slack
  exports; ingestion workers that index every chunk with its access list.
- The Knowledge Hub: collections, documents, indexing status, the chunks the assistant reads.

## v0.1.0 — Phase 1: identity, tenancy and permissions (2026-11-04)

- Tenant data model with forced Row-Level Security on every tenant table.
- Keycloak realm as code; `make seed` creates Acme Robotics and its demo logins.
- OIDC login through the backend, server-side sessions, `/me`.
- OpenFGA authorization model, a store per tenant, outbox sync and reconcile; the authorization
  matrix is tested in CI.
- Administration API and screens for people, departments, groups and the audit log; personal
  access tokens for MCP clients, CLIs and scripts.

## v0.0 — Phase 0: foundation (2026-11-04, not tagged)

- Monorepo with the FastAPI API and the Next.js shell; Docker Compose with Postgres, Qdrant,
  Valkey, Keycloak, OpenFGA and Caddy; `make up`.
- CI: lint, types, tests, gitleaks, the DCO sign-off check. Apache-2.0 core with `ee/` under a
  commercial license; contribution and security policies; the first ADRs; the Acme Robotics
  company definition.
