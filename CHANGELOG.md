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

Upgrade notes so far:

- The next `make up` pulls the digest-pinned images. `postgres:18.6` resolves to a newer build of
  the same tag than older checkouts pulled; the data volume is unaffected.
- The api image runs as `10001:10001` (the user ID is unchanged; the group is now 10001) and the
  web image as `1001:1001` (unchanged). Existing volumes keep working.

The rest of Phase 12's scope (security review, load tests, multi-tenant hardening, backup and
restore, the upgrade guide, the Helm chart, the docs site) is not listed here yet.

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
