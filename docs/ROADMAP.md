# WorkMind AI — Roadmap, Releases and Go-to-Market

> **Status:** plan v0.2 · **Phases 0 to 12 built** (7 and 12 with work remaining; no release after v0.1.0 is tagged yet) · **Last updated:** 2026-11-13
> **Companion doc:** [ARCHITECTURE.md](ARCHITECTURE.md)

## 1. Strategy in one paragraph

Build the **complete product in public, one phase at a time**. Every phase ends in a **tagged release that works on its own**, plus a **LinkedIn post** showing something real: a demo, a diagram, a measured number. The repo is open source from the start, under an **open-core** model. The free Community edition is genuinely useful and self-hostable. Later, a paid Enterprise edition and a hosted WorkMind Cloud add what large organizations need (compliance, scale, administration, support). Recognition and career value come first. The business is the upside once there are real users.

**Our wedge** (why someone picks WorkMind over other open-source AI assistants):
- **Provably permission-safe:** a public leak-test suite where the leak rate must be 0 to merge.
- **Measured:** every release publishes quality, latency and cost numbers.
- **Self-hosted and model-agnostic:** run fully local, or plug in paid providers. Sensitive data never leaves your servers.

---

## 2. Phase overview

Sizes are rough estimates in **weeks of focused work**; part-time takes longer. The total is about 27–34 focused weeks, roughly 6–8 months.

| Phase | Release | Theme | Size | Public milestone |
|---|---|---|---|---|
| 0 ✅ | v0.0.x | Foundation and synthetic company | 1–2 wk | Repo public (pre-alpha), architecture post |
| 1 ✅ | v0.1 | Identity, tenancy and permissions | 2 wk | Login + permission model demo |
| 2 ✅ | v0.2 | Knowledge ingestion | 2 wk | 500-document corpus ingested, live status (as built: 49 documents) |
| 3 ✅ | v0.3 | Retrieval engine + evaluation harness | 2–3 wk | First published eval numbers |
| 4 ✅ | **v0.4** | **AI Assistant — first usable product** | 2 wk | **Public launch** (HN, Reddit, LinkedIn video) |
| 5 ✅ | v0.5 | Multi-agent orchestration | 2–3 wk | "Do you need multi-agent? I measured it." |
| 6 ✅ | v0.6 | Tools and MCP | 2 wk | WorkMind inside Claude Desktop / Cursor |
| 7 🚧 | v0.7 | Connectors and sync | 3 wk | Google Drive + Slack live sync |
| 8 ✅ | v0.8 | Workflow automation and approvals | 2–3 wk | Human-in-the-loop demo |
| 9 ✅ | v0.9 | Meeting intelligence | 2 wk | Recording → decisions and action items |
| 10 ✅ | v0.10 | Business intelligence agent | 3 wk | Question → chart + SQL + explanation |
| 11 ✅ | v0.11 | Admin, governance and usage analytics | 2 wk | New agent created without code |
| 12 🚧 | **v1.0** | **Production hardening** | 2–3 wk | **v1.0 launch** (HN, Product Hunt) |
| — | after 1.0 | Commercial track | ongoing | WorkMind Cloud + Enterprise edition |

✅ built (4: launch pending the public repository; 8 to 11: with the gaps their sections list);
🚧 built with work remaining (7: live Drive, Slack, Confluence and SharePoint verification; 12:
the release). Each phase's section below has the details, including a **Not built** list of the
scope items that were dropped or deferred. "Built" is not "done" in the sense of §3: only v0.1.0
is tagged, so no later phase has its release yet ([releasing](operations/releasing.md)). On 2026-11-13 the last Phase 7 to 11
branches were merged into `main` and the development stack was rebuilt from it; what that run
checked is under each phase ("Integrated 2026-11-13").

---

## 3. Phases in detail

Each phase lists its **goal**, **scope**, **definition of done (DoD)** and **LinkedIn post**. A phase is not done until its DoD is met *and* its release is tagged.

### Phase 0 — Foundation and synthetic company (v0.0.x) ✅ Complete

**Goal:** anyone can clone the repo, run one command, and get a healthy (empty) stack.

**Scope:**
- Monorepo layout (ARCHITECTURE §24), `uv` + `pnpm`, Makefile (`make up`, `make test`, `make seed`).
- FastAPI skeleton: config (pydantic-settings), structlog, OpenTelemetry, `/healthz` and `/readyz`.
- Next.js shell: navigation layout with all sections as placeholders.
- Docker Compose core: Postgres, Qdrant, Valkey, Keycloak, OpenFGA, Caddy.
- Alembic setup, first migration.
- CI: lint, typecheck, tests, gitleaks.
- `LICENSE`, `ee/LICENSE`, `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, issue/PR templates, DCO sign-off check.
- First ADRs (architecture style, stack, auth pattern, license).
- **Acme Robotics company definition:** departments, groups, 20 people (6 demo personas), 13 collections with access rules (`seed/acme/company.yaml`). The document generator moved to Phase 2, where documents are first needed; it generates them from a ground-truth facts file so every eval question has a known answer.

**DoD:** fresh clone → `make up` → all services healthy in under 5 minutes. CI green.

> Measured 2026-11-13 by following the README from fresh clones on a laptop that was busy with
> other jobs ([quickstart](getting-started/quickstart.md#how-long-it-takes)): with
> `make quickstart`, every service was healthy 1 min 54 s after the clone with an empty build
> cache (1 min 24 s with the cache kept), and the demo company was seeded after 3 min 3 s
> (2 min 40 s). The 0.9 GB of service images were already on the machine; their download was
> not timed.
>
> OpenTelemetry, as built: optional spans of the API's HTTP requests over OTLP, and the trace id
> in log lines. No metrics, alerts, Langfuse or worker traces (ARCHITECTURE §21).

**LinkedIn post #1 — "I'm building an open-source enterprise AI workspace, in public."**
- The problem: company knowledge is scattered, and AI assistants leak permissions.
- The architecture diagram and the 8 design principles.
- Ask: *"What would make you trust an AI assistant with your company's documents?"*

### Phase 1 — Identity, tenancy and permissions (v0.1) ✅ Complete

**Goal:** real login, real organizations, and a permission model that everything else builds on.

**Progress:**
- [x] Slice 1: tenant data model with forced Row-Level Security, plus isolation tests
- [x] Slice 2: Keycloak realm as code, `make seed`
- [x] Slice 3: login/logout (BFF), sessions, back-channel logout, `/api/v1/me`, end-to-end tests
- [x] Slice 4: web sign-in and user menu
- [x] Slice 5: OpenFGA model, outbox sync, reconcile, authz matrix tests
- [x] Slice 6a: admin API (people, departments, groups, audit log) with guard rails and a role × endpoint test matrix
- [x] Slice 6b: admin UI (people, departments, groups, audit log with integrity check) and access tokens in Settings
- [x] Slice 7: personal access tokens (scoped, hashed, expiring), with an end-to-end test of an MCP-style client

**Scope:**
- Keycloak realm as code with demo users (ARCHITECTURE §6.7).
- BFF login with Authlib: PKCE, sessions in Valkey, CSRF protection, logout, back-channel logout.
- Tenants, users, departments, groups (nested), org roles.
- Postgres RLS on all tenant tables.
- OpenFGA model + transactional outbox sync. Principal resolution + cache.
- Personal access tokens.
- Admin UI: users, departments, groups, invitations.
- `audit_events` foundation (logins, admin changes).
- Tests: the **authz matrix** (role × resource × action) and **tenant-isolation tests**.

**DoD:** log in as each demo user and see the correct capabilities. The audit log shows logins and admin changes. Isolation tests pass.

> **Not built:** invitations by email. **Add person** creates the WorkMind user (status
> *invited*), who can sign in only once a Keycloak user with the same verified email exists
> (created by an operator, or through a federated identity provider); no email is sent
> ([administration guide](user-guide/administration.md#adding-people)). The principal set is not
> cached (computed per request, ARCHITECTURE §11.7), and reconcile does not run on a schedule.

**LinkedIn post #2 — "Most RAG demos skip the hardest enterprise problem: who is allowed to see what."**
- Show the login flow diagram and the OpenFGA model.
- Explain the "two locks" idea.

### Phase 2 — Knowledge ingestion (v0.2) ✅ Done

**Goal:** documents go in reliably, with structure, metadata, versions and permissions.

**Scope:**
- Collections with scopes (personal / department / enterprise / restricted).
- Upload of PDF, DOCX, PPTX, XLSX, Markdown, HTML, TXT, plus Slack export JSON.
- Storage adapter: local + S3, two implementations with contract tests.
- Celery ingestion workers. Docling parsing + OCR. HybridChunker with heading context.
- Metadata enrichment, Presidio PII scan → sensitivity label, trust levels.
- Versioning, deduplication by content hash, deletion propagation.
- ACL payload projection.
- Embedding adapter: BGE-M3 (Ollama/TEI) + OpenAI, two implementations. BM25 sparse vectors.
- Qdrant collection + alias.
- Knowledge Hub UI: collections, documents, versions, chunk viewer, live ingestion status.
- Acme corpus grows to about 500 documents, including restricted documents with canary tokens.

**DoD:** the full Acme corpus ingests with zero unhandled failures. Re-uploading an unchanged file is skipped. Throughput (docs/min) is measured and recorded.

> **Not built:** the S3 storage adapter (only a local directory; the local and in-memory stores
> share a contract suite, `tests/test_storage_contract.py`); Docling, OCR and the HybridChunker
> (lightweight parsers instead, [ADR 0007](adr/0007-model-providers-and-lightweight-parsing.md));
> the Presidio PII scan (a regex scan for IBANs, US SSNs and pay figures suggests the label; no
> names, addresses, phone numbers or emails); metadata enrichment beyond the title; the Qdrant
> alias (a collection per model, `wm reindex` to switch); a ~500-document corpus (49 documents
> in 14 collections, [case study](case-study.md)).

**LinkedIn post #3 — "What actually happens between 'upload PDF' and 'AI answers'."**
- Pipeline diagram.
- Lessons on chunking tables and on versioning.
- Real throughput numbers.

### Phase 3 — Retrieval engine and evaluation harness (v0.3) ✅ Done (see `evals/README.md`)

> Done: hybrid search with tuned fusion, both locks, citations, abstention, sensitivity routing,
> `wm eval` with QA / red-team / abstention suites, the CI permission gate, and the ablation.
> Deferred: the reranker adapter (no measurable headroom yet: Evidence@3 is 98.8%), the
> local-vs-paid comparison (D3: the maintainer runs Claude), and growing QA from 82 to ~200 items.
> Not built: metadata filters extracted from the question (department, dates, document type)
> and the freshness boost (old documents are reminded to their owners, not ranked lower); the CI
> model smoke set and any recall or faithfulness gate. CI runs the red-team and retrieval checks
> retrieval-only (hash embeddings) and gates leaks at 0; the model-backed suites are run by hand
> against the stack and their reports committed in `evals/results/`. "Baseline targets" are the
> measured numbers in those reports; no threshold enforces them.

**Goal:** high-quality, permission-safe retrieval, with numbers to prove it.

**Scope:**
- Hybrid search (dense + BM25, RRF). Reranker adapter (bge-reranker + Cohere).
- ACL pre-filter + OpenFGA recheck. Metadata filters, trust and freshness boosts.
- Citation generation and verification. Abstention.
- Sensitivity-based model routing.
- Eval harness:
  - Datasets: retrieval QA (~200), red team with canaries, abstention.
  - Metrics (ARCHITECTURE §22).
  - `wm eval` CLI.
  - CI smoke + red-team gate.
- **Ablation report:** dense-only vs. hybrid vs. hybrid + rerank.
- **Local vs. paid model comparison.** Decide the default local model (D3).

**DoD:**
- The eval report is committed.
- The red-team leak rate is 0.
- Baseline targets are set from measured numbers, not invented.

**LinkedIn post #4 — "Vector search alone got Recall@5 of X. Here's what moved it to Y."**
- Real numbers from the ablation.
- Plus how canary tokens catch permission leaks automatically.

### Phase 4 — AI Assistant: first usable product (v0.4) — PUBLIC LAUNCH ✅ Built (launch pending the public repo)

> Done: streaming chat with citations, sources panel, follow-ups, feedback, usage and cost, "view as",
> `make quickstart`, the README GIF and the demo video. Pending the maintainer: public demo
> deployment (D4) and the launch.
>
> DoD, measured 2026-11-13 ([quickstart](getting-started/quickstart.md#how-long-it-takes)):
> following the README from a fresh clone, the first cited answer came about 3 min 50 s after
> the clone with an empty build cache and the key added after `make quickstart`, and about
> 2 min 45 s with the build cache kept and the key in `.env` first; these add up the measured
> steps on a busy laptop, with the service images already downloaded. A person's reading and
> typing time was not measured. Following the README also found what a newcomer would trip on,
> now fixed: the requirements left out `git` and `make`, a first question without a key failed
> with "Something went wrong while answering", `make up` and `make status` ignored a moved
> `HTTP_PORT`, **View as…** did not offer Olivia, whose examples the quickstart uses, and the
> document count was stale. The [demo video](media/demo.mp4) (1 min 44 s) and the README GIF
> were recorded with `make demo-video` against the development stack
> ([how](../e2e/browser/demo/README.md)). These timings were taken before the seed grew to 49
> documents (two with their own access lists) on 2026-11-13; they were not taken again.
>
> Not built: the PDF highlight in the citations panel. It shows the cited passage's text and
> links "Open PDF at page N", which opens the PDF at that page with nothing highlighted (the
> parsers record page numbers, not positions).

**Goal:** a real person can install WorkMind and get cited, permission-aware answers in minutes.

**Scope:**
- Chat UI with streaming (run + SSE events), conversation history.
- Standalone question rewriting, rolling summaries.
- Citations panel with PDF highlight. Feedback buttons.
- "View as" demo switcher. Usage and cost per message.
- One-command quickstart with seed data.
- README with GIF, docs site v1.
- Public demo deployment (D4).

**DoD:**
- A new user goes from README to first cited answer in under 10 minutes.
- A 2-minute demo video is recorded.

**Launch:**
- LinkedIn launch post #5 with the video.
- Show HN, r/selfhosted, r/LocalLLaMA (following each community's rules).
- dev.to / blog article.

### Phase 5 — Multi-agent orchestration (v0.5) ✅ Done (ADR 0009)

> Measured: routing 36/36; agents beat the single pipeline only on three-area questions, at 3x latency
> and 4-5x cost, so `single` stays the default and `auto` is offered.
>
> Not built: the validator with re-plan (aggregation drops findings with unknown citation
> markers; nothing re-plans), Postgres checkpoints (dropped by ADR 0009: runs are short and
> approvals are stored actions), and the provider fallback chain and circuit breaker (the SDK
> retries 429 and 5xx three times, and Claude falls back on refusals server-side; a provider that
> is down fails the request). `Send` fan-out is plain `asyncio.gather` (ADR 0009).

**Goal:** complex, cross-department requests answered by cooperating agents, with proof that it helps.

**Scope:**
- Run orchestration (plain asyncio, ADR 0009), supervisor planner (structured output), fast-path router.
- Agent registry (agents as configuration). HR, Engineering and Finance agents.
- Fan-out with `Send`, evidence aggregation, validator with re-plan.
- Postgres checkpoints, budgets, timeouts, partial results. Provider fallback chain.
- Agent trace drawer in the UI.
- Routing eval set.
- **Ablation: single agent vs. supervisor + specialists** on cross-department questions.

**DoD:**
- A cross-department report question is answered with citations from three agents.
- Routing accuracy is measured.
- The ablation is published honestly, whatever the result.

**LinkedIn post #6 — "Do you actually need multi-agent? I measured it."**

### Phase 6 — Tools and MCP (v0.6) ✅ Done (see `docs/mcp.md`)

> Done: tool registry with side-effect levels, execution wrapper (scopes, approval gate, sanitizing,
> audit), `search_knowledge` / `draft_email` / `web_search` (SearXNG, optional), research agent,
> MCP client for operator-configured servers, WorkMind MCP server with token auth, injection tests.
> `create_task` moves to Phase 8 with the Tasks module.

**Goal:** agents can act through governed tools, and WorkMind plugs into the MCP ecosystem.

**Scope:**
- Tool registry with side-effect levels and policies.
- Execution wrapper: authz, approval-gate stub, audit, output sanitizing.
- Built-in tools: `search_knowledge`, `create_task`, `draft_email`, `web_search` (SearXNG).
- Research agent.
- MCP client: register remote MCP servers, import their tools.
- **WorkMind MCP server** at `/mcp` with personal access token auth.
- Injection tests on tool outputs.

**DoD:**
- Ask a permission-aware question from Claude Desktop or Cursor through the WorkMind MCP server.
- An agent successfully uses an external MCP tool.

**LinkedIn post #7 — "Your company knowledge inside your IDE, with permissions intact."**

### Phase 7 — Connectors and sync (v0.7) 🚧 Built; live Drive/Slack/Confluence/SharePoint verification pending (see `docs/connectors.md`)

> Done: connector SDK and contract suite, sync engine (versions, deletion propagation after complete
> scans and from delta tombstones, failures recorded and retried from the same cursor, one sync
> per source, each document's place in its source kept as metadata), Celery beat scheduling,
> AES-GCM credentials, web crawler (tested against a live site), GitHub (tested against a public
> repository), Slack and Google Drive with OAuth (tested against their documented APIs),
> Confluence Cloud (REST API v2, OAuth 2.0 3LO or API token, attachments, page tree) and
> SharePoint/OneDrive (Microsoft Graph delta queries, OAuth with PKCE, folder selection,
> throttling), both tested against fakes built from their API documentation and through the sync
> engine, not against a live site or tenant; collection-level permission mapping. The source
> health dashboard (Admin › Sources, and Knowledge Hub › Sources for collection owners: health,
> mapping, last and next sync, run history, plain errors, pause/resume, disconnect), "missing
> upstream" flags after cut-short scans, and daily stale-document reminders to owners plus
> failing-source alerts, each once per episode. Per-document permission sync (Enterprise
> `permission_sync`, [docs/permission-sync.md](permission-sync.md), ADR 0012): document access
> lists that only narrow their collection, enforced in both locks and every other path
> (Community), fail-closed identity mapping with unmapped principals listed for admins
> (Community), ACLs read from Drive, Slack and GitHub during every sync and Drive revocations
> from the changes feed every minute (Enterprise), tested against fakes of the three APIs.
> Pending: a test Drive and Slack workspace (DoD; permission sync has not met a live API
> either), a live Confluence site and Microsoft 365 tenant, a screen for a document's own list,
> and moving a source to another collection.
>
> Integrated 2026-11-13: the Sources page offers all six kinds (Confluence with an API token or
> Connect Confluence, SharePoint/OneDrive with Connect SharePoint), says on each source whether
> permission sync applies to it, and hides Source accounts from collection owners (the API is
> for org admins). Stale-document lists and reminders now respect a document's own list and skip
> archived documents; sync deletions keep documents under a legal hold, tombstones included.
> Failing-source and stale-document notifications follow each person's email and Slack choices.
> Checked: the backend suite (1,924 passed, 25 skipped, unit, integration and `ee/`), the web
> tests, and on the rebuilt, re-seeded stack the browser suite (184 passed, including the
> Admin › Sources and Knowledge Hub › Sources pages) and the retrieval-only permission gate (0
> leaks in 183 checks, the 9 document-list attacks included,
> [report](../evals/results/2026-11-13-permission-gate-final.md)). A Confluence source was
> created and removed through the API on that stack. No connector was synced against a live
> service.
>
> **DoD not met:** no test Google Drive or Slack workspace has been synced live, so "stay in sync
> within the interval" and "deletions in the source disappear" are verified against fakes and
> the live web crawler and GitHub only. Not built: the stretch goal, a Slack or Teams bot (there
> is no inbound Slack or Teams endpoint; Slack is used to sync and to notify).

**Goal:** knowledge flows in automatically from the tools companies already use.

**Scope:**
- Connector SDK + contract test suite.
- Scheduler for incremental sync.
- OAuth flows with encrypted credentials.
- Connectors: Google Drive, Slack, Confluence, GitHub, SharePoint/OneDrive (Microsoft Graph), web crawler.
- Deletion propagation.
- Source health dashboard. Stale-document detection and owner reminders.
- **Community:** collection-level permission mapping (e.g. "this Drive folder → Finance department").
- **Enterprise (`ee/`):** per-document permission sync that mirrors source ACLs.
- *Stretch:* Slack/Teams bot — ask WorkMind inside chat.

**DoD:**
- A test Google Drive and Slack workspace stay in sync within the configured interval.
- Deletions in the source disappear from WorkMind.

**LinkedIn post #8 — "Connecting to Google Drive is easy. Keeping permissions in sync is the hard part."**

### Phase 8 — Workflow automation and approvals (v0.8) ✅ Done (see `docs/automation.md`)

> DoD met live: "prepare the weekly ops report and email it to leadership" → four searches → a
> drafted email → approval with an edit → sent (Mailpit) → every step audited.
>
> Done: approvals (stored proposals, atomic decisions, edits re-validated, expiry after 3 days);
> the approvals inbox and approval pages; inline approval cards in chat, which come back after a
> reload with the research steps that proposed them; tasks with department queues (claim, hand
> over); SMTP `send_email` and `create_task`; research mode; workflows with manual, cron and
> `document.ready` triggers (one run per document version, as the owner, only for documents the
> owner can view), paused and resumed at approvals; Admin › Workflows (edit, run now, run history,
> delete); in-app notifications, each opening a page that exists. The Operations agent is a
> department agent in `seed/acme/company.yaml`; the weekly report is a template in Admin ›
> Workflows, not a seeded workflow.
>
> Added in the v1.0 pass (2026-11-13): reminders and escalation (hourly, once per approval step
> and per task, to the backup approver, the managers or the org admins; expiry stays the last
> step); notifications by email (immediate or a daily digest) and Slack (bot DMs, a channel
> webhook), per person and category, from an outbox with retries; approver rules (org admins,
> department managers, any-of groups, chains; never the requester) for workflow steps and the
> assistant's actions; request routing (admin rules, then the router model above a threshold,
> else triage; the Request form, an email stub, `create_task`), each decision explained and
> audited, with a 39-item eval set; workflows in YAML with errors at their lines; the events
> `document.updated`, `meeting.processed`, `task.completed` and `approval.decided`. Checked with
> unit and integration tests (a scripted model, a stub mailer and a stub Slack); the routing eval
> has not been run with a real model.
>
> Integrated 2026-11-13: on the rebuilt stack the browser suite (184 passed) covers Requests and
> Admin › Automation (page, accessibility and layout checks); beat schedules
> `workmind.escalate_work` hourly and `workmind.deliver_notifications` every minute, and the
> seed added the 7 Acme routing rules. Every notification kind now has a delivery category,
> including two new ones (Sources, Legal holds). Not observed on the stack: an escalation, an
> email or a Slack message actually sent by the beat tasks.
>
> Not built: Microsoft Teams notifications and Slack interactivity (approving from Slack);
> reading a mailbox for email requests (only an endpoint for a bridge); per-workflow SLAs and
> working hours; events for inbound email.

**Goal:** WorkMind does work, safely, with humans in control.

**Scope:**
- Approval service, approvals inbox, inline approval cards (approve / edit / reject).
- Agent actions as stored proposals that a decision resumes (ADR 0009).
- Tasks module. SMTP email adapter. `send_email` with approval.
- Request routing to department queues.
- Declarative workflows (YAML) with manual / schedule / event triggers.
- Scheduled weekly report. Notifications. Expiry and escalation. Operations agent.

**DoD:** *"Prepare the weekly ops report and email it to leadership"* → draft → approval (with an edit) → sent → fully audited.

**LinkedIn post #9 — "Designing human-in-the-loop for AI agents: when should the AI ask permission?"**

### Phase 9 — Meeting intelligence (v0.9) ✅ Done (see `docs/meetings.md`)

> Built: transcript and recording upload with a consent notice, transcription (faster-whisper),
> structured extraction with timestamps checked against the transcript, action items → tasks,
> "My commitments" on `/tasks`, transcripts indexed as knowledge, the meeting-extraction eval set,
> and the pages (`/meetings`, `/meetings/{id}`) with clickable timestamps that play the recording
> or scroll the transcript. Measured: extraction and transcription times in `docs/meetings.md`. The
> UI was checked end to end in Chromium with a scripted model, not a real one. Added in the
> final pass: speaker diarization (pyannote segmentation and NeMo TitaNet-S as ONNX through
> sherpa-onnx, no Hugging Face token; measured on the demo recording), naming speakers as
> colleagues or guests (relabels the transcript, the notes and the indexed transcript; audited),
> and topic outlines (model-based, checked against the transcript, with a deterministic
> fallback; scored in the meetings eval), checked in headless Chromium with a scripted model.
> Not measured with a real model: the topic outline and extraction from diarized recordings.
>
> Integrated 2026-11-13: the API image now installs the `diarization` extra. In the rebuilt
> worker container sherpa-onnx 2.0.1 loads, and the two models downloaded into
> `/models/diarization` and loaded (153.6 s, most of it the download, on a busy laptop). No
> recording was diarized on the stack.

**Goal:** meetings turn into decisions, tasks and searchable knowledge.

**Scope:**
- Transcript and recording upload. ffmpeg + faster-whisper + pyannote.
- Speaker mapping UI.
- Structured extraction with timestamp citations. Action items → tasks.
- "My commitments" tracker.
- Transcripts indexed as knowledge.
- Meeting-extraction eval set.

**DoD:**
- A 30-minute recording produces a summary, decisions and action items with clickable timestamps.
- Processing time is measured on reference hardware.

> As measured: on the 23-minute demo recording, not a 30-minute one (transcription 42 to 51 s on
> the laptop's CPU, 78 s in the Docker worker; [meetings.md](meetings.md)). No 30-minute recording
> was run.

**LinkedIn post #10 — "From a 30-minute meeting to tracked commitments, fully self-hosted."**

### Phase 10 — Business intelligence agent (v0.10) ✅ Done: execution accuracy 33/36 with Claude Sonnet 5.5 on CSV/XLSX data, 0 permission leaks in 51 answers; PostgreSQL and MySQL sources and live progress built and tested with scripted models (`docs/analytics-sql-sources.md`)

**Goal:** analytical questions answered with charts, explanations and verifiable SQL.

**Scope:**
- Data source connectors (Postgres/MySQL; CSV/XLSX → DuckDB).
- Semantic layer YAML. Text-to-SQL via the semantic layer.
- sqlglot validation, read-only execution with limits, row-level policies.
- Chart specs + rendering. Narrative with number verification.
- Insights page. Analytics agent.
- Text-to-SQL eval set (execution accuracy).

**DoD:** *"Monthly revenue trend this year and the top 3 drivers"* → chart + explanation + SQL + data link. Execution accuracy is published.

**Status:**
- Done: CSV/XLSX → versioned DuckDB per tenant; semantic layer YAML; sqlglot guard with row policies; sandboxed read-only engine; the BI agent (tool loop, compose, chart spec, number verification with one repair pass); `/api/v1/analytics/*` and admin routes; `describe_data` / `query_data` for research mode and MCP; the Insights page and Admin › Analytics; the text-to-SQL eval (`wm eval --suites analytics`, [ADR 0010](adr/0010-analytics-semantic-layer-duckdb-sqlglot.md)).
- Measured with Claude Sonnet 5.5 ([evals/results/2026-11-12-analytics.md](../evals/results/2026-11-12-analytics.md)): execution accuracy 33/36 (28/29 with full access, 5/7 under Dan's row policy), 5/5 people without access refused with no model call, 10/10 attacks without a leak, 0 leaks, 159/159 narrative numbers matched to a result, 6.9 s p50 / 10.2 s p95, $0.026 per answer. The DoD question was asked on the Insights page against the running stack: a chart, the narrative, 4 queries with their SQL and data, 20/20 figures backed. Two runs only, one model.
- Done since (2026-11-13, [docs/analytics-sql-sources.md](analytics-sql-sources.md)): PostgreSQL and MySQL sources as datasets of their own (own model, access and row policies), read-only three ways (the guard, a read-only session, a read-only database role the docs ask for), statement timeouts, row caps, a connection budget per tenant, hosts checked against the model providers' SSRF rules with private networks only by an operator setting, passwords sealed like provider keys; model tables rewritten to the database's, row policies as CTE fences (PostgreSQL `MATERIALIZED`: without it, PostgreSQL 18 evaluated an oracle on hidden rows in a test); admin connect, catalog and model-check routes and UI; the agent across datasets; live progress of an answer on Insights (planning, each query compiled and run, writing, verifying, charting) over the chat runs' event streams. Tested on PostgreSQL 18.6 and MySQL 8.4.13 with scripted models: 127 guard bypass cases refused, 51 integration tests and 4 over HTTP; 19 eval items for a SQL source (`evals/datasets/analytics_sql.jsonl`) score 19/19 with a gold-answering scripted model.
- Integrated 2026-11-13: the API image installs the `mysql` extra; after the merge the 55 SQL-source integration tests passed again against PostgreSQL 18.6 and a throwaway MySQL 8.4.13 container. No SQL source was connected on the running stack (none is seeded).
- Not done: execution accuracy on a SQL source with a real model; measurements with other or local models; databases other than PostgreSQL and MySQL; connection pooling.

**LinkedIn post #11 — "Letting an LLM write SQL against company data, safely."**

### Phase 11 — Admin, governance and usage analytics (v0.11) ✅ Built and merged; the DoD is measured with the Legal agent created in the browser (routes 15/15, 53/56 overall); the review findings are fixed

**Goal:** organizations can run WorkMind themselves: configure, control and account for it.

**Scope:**
- Agent configuration UI: prompt, tools, scopes, model, limits, versions, test console.
- Model and provider management: bring-your-own keys, routing policies.
- Usage and cost analytics per department / user / agent / model. Quotas and budgets.
- Audit log explorer. Retention policies.
- Knowledge governance: owners, review reminders, "official" approval flow for documents.
- Support, Sales and Marketing agents created **via config only**.

**DoD:** an admin creates a new "Legal agent" from the UI without code, and it routes correctly in the routing eval.

> **Status on 2026-11-12, as merged into `main`** (everything below is in `main`; what was
> checked on the running stack is said so):
>
> - **Agent configuration ([docs/agents.md](agents.md)):** tools (read-level only, run as the
>   asker), a model per agent from the organization's catalog (checked when saved, applied when
>   it runs), limits, numbered versions with diff and restore, and a test console that routes
>   and answers with an unsaved draft. A Legal & Contracts collection (10 documents) and 15
>   legal routing questions are seeded; the Legal agent itself is not.
> - **DoD, measured:** `erin@acme.test` created the Legal agent in Admin › Agents (no code, the
>   fields docs/agents.md gives; the editor suggested the key `legal-agent`, changed by hand to
>   `legal`, and now suggests `legal`). Against that agent the routing evaluation picks Legal for
>   15 of 15 legal questions (14 exact plans) and for none it should not, keeps 3/3 near misses
>   away, and gets 53/56 plans exactly right with Claude Sonnet 5.5
>   ([report](../evals/results/2026-11-12-routing-legal-ui.md)): the same as the agent made
>   through the API earlier (54/56 with Opus 5.5 planning). The three misses each add one agent
>   (patent bonus: Legal and HR; engineering budget: Engineering and Finance; cost per lead:
>   Finance and Marketing). One run per configuration. Its 46 planner calls were recorded in usage
>   as feature `eval` ($0.157). The first API run caught a planner rule that let a new agent change
>   an unrelated plan; fixed.
> - **Models ([docs/models.md](models.md)):** providers with encrypted bring-your-own keys, a
>   catalog with prices, a model per role and the data policy, resolved per organization for
>   every model call. Tested with scripted models; no real provider was called through a tenant
>   key.
> - **Usage and budgets ([docs/usage.md](usage.md)):** one usage event per model call (failed
>   and stopped calls included, never the text), attributed to the person, their department,
>   the agent, the feature and the record; costs from the catalog's prices; the Usage page,
>   Admin › Budgets and "Your usage this month" on the Overview; organization budgets, personal
>   and default caps that warn or block (429 `budget_exceeded`, the chat run's error event, or
>   an MCP tool error), checked before every model call, including every turn of a tool loop
>   (fixed 2026-11-12: a loop was checked once); `wm usage backfill` for older answers.
>   On the running stack, one real Claude Sonnet 5.5 answer was recorded as one event with the
>   provider's tokens and the right cost, and the backfill imported 33 older records (21 chat
>   answers, 10 Insights answers, 2 meetings). Failed and stopped calls are tested with scripted
>   models only.
> - **Knowledge governance and the audit explorer ([docs/governance.md](governance.md)):**
>   document owners, review cycles with a daily reminder (a task and a notification, once per
>   cycle, following the document to a new owner), official status only through an approval
>   for one version of the content (never self-approved, only by people who can read the
>   collection; new content ends official status; demotion immediate and audited), a
>   governance overview for admins and collection owners, "Review overdue" on cited official
>   documents, and audit filters, cursor paging and a CSV export. All Community.
>   On the running stack, through the API: an overdue official source was flagged in a real
>   answer, and an official-status request was refused when repeated, rejected by an admin and
>   superseded for the other approver.
> - **Open core ([docs/enterprise.md](enterprise.md)):** license keys, development licenses, the
>   extension registry and `GET /api/v1/edition`. Four Enterprise features are built in `ee/`
>   and plug in through the registry: department budgets, the chargeback CSV, retention
>   policies (with a preview of a draft and a typed confirmation to run) and the export of the
>   audit hash chain for a SIEM. Without a license they answer `402 enterprise_feature`.
> - **Review and browser walkthrough (2026-11-12):** a security review and a walkthrough of the
>   Phase 11 screens in a browser found, among others: an editor could replace an official
>   document's content and keep its official status and review; official-status requests went
>   to admins who could not open the collection (and showed them its title); requests and
>   review reminders stayed with former owners; tool loops were budget-checked once; a budget
>   crossed mid-answer showed only "This answer failed"; rejections were audited as
>   `approval.rejectd`; the audit explorer ignored its URL after client-side navigation; two
>   tables widened the page at 1280 px. All of these are fixed with regression tests (the
>   audit-URL and table-width fixes were checked in headless Chromium, since the web tests run
>   without a DOM), plus smaller ones (a failed agent's raw provider error shown to the asker,
>   official decisions with access tokens, budgets below $0.0001, the default cap's warning
>   level, second-department budgets shown as limiting, weekly charts without empty weeks,
>   ranges over 400 days, an edition request failure shown as "not licensed", the agent key
>   suggestion, review tasks without a link, empty conversations left by refused questions).
>   Not changed: the agent editor's older Restricted badge contrast (3.19:1, from Phase 4).
> - **Gates on `main` after the fixes:** 1115 backend tests (852 unit, the rest integration and `ee/`),
>   1023 web tests, `pyright` and `ruff` clean, `pnpm build`; migration 0019 (the reason a failed
>   answer failed) up, down and up again with `alembic check` clean.
> - **Built on 2026-11-13, merged into `main` and running on the development stack:**
>   - *Review escalation* (Community, [governance.md](governance.md#escalation-of-overdue-reviews)):
>     an official document whose review stays overdue escalates once per stage and cycle, to the
>     collection's owner after the collection's grace period (default 14 days), to the org admins
>     one period later, and, only when the collection sets it, is demoted to reference; each
>     stage audited and notified once.
>   - *Legal hold* (Enterprise feature `legal_hold`, [enterprise.md](enterprise.md#legal-hold)):
>     holds on documents, collections, people (their conversations, meetings and Insights
>     questions), meetings and Insights questions, with a matter and a reason, in Admin › Legal
>     holds. The core enforces them whatever the license says: deletions by people, connectors'
>     deletion propagation, retention runs and `wm tenant delete` (without the typed override)
>     are refused, and every attempt is audited.
>   - *Document retention* (Enterprise, part of `retention_policies`,
>     [enterprise.md](enterprise.md#document-retention)): per collection, delete or archive
>     documents not updated for N days, with an exact preview, legal holds and official status
>     respected, archive and restore, all audited.
>
>   Checked on the branch by the backend suite (1430 tests), the web tests (1079), `pyright`,
>   `ruff`, `pnpm build`, migration 0026 down and up with `alembic check` clean, and the three
>   screens in headless Chromium with mocked API answers. After the merge: the full backend
>   suite (1,924 passed), migrations `0012` to `0026` down and up again on a throwaway database
>   with data, and on the rebuilt stack with the Enterprise dev license (which now includes
>   `legal_hold` and `permission_sync`) the browser suite's Legal holds checks, licensed and
>   Community (184 passed in all; they needed the page added to the suite's registry). Placing
>   or releasing a hold now notifies the other owners, admins and auditors. Not observed on the
>   stack: the daily task escalating a review or applying a document rule (the first seeded
>   escalation is due on 2026-11-17).
> - **Not done:** a reminder sent by the daily task on the running stack (it first runs a day
>   after beat starts). DLP, SCIM and the other paid features of §5 not listed above are not
>   built and not part of this phase (permission sync is built, under Phase 7).

**LinkedIn post #12 — "Adding a new AI agent to a company without writing code."**

### Phase 12 — Production hardening (v1.0) 🚧 The hardening pieces are built and merged, the load test is run and tuned, the v1.0 benchmarks and the case study are written, the Helm chart is tested on kind, the final review's findings are fixed, and the end-to-end suites pass on the stack rebuilt with the Phase 7 to 11 merges (2026-11-13); the release remains

**Goal:** a release companies can trust in production.

**Status (2026-11-13):**
- [x] **Security review** against the OWASP Top 10 for LLM Applications (2025) and a threat
  model: 11 fixes, each with a regression test ([review](security/owasp-llm-top10.md),
  [threat model](security/threat-model.md)). Red team grown from 16 to 59 attacks on four
  surfaces (68 since 2026-11-13, with 9 attacks on documents' own access lists; the numbers below
  are the 59-attack runs). Live, after the merges, on the rebuilt and re-seeded stack: the retrieval permission
  gate 0 leaks in 156 checks ([report](../evals/results/2026-11-12-permission-gate.md)); all 59
  attacks answered with Claude Sonnet 5.5, 0 leaks and 2 violations after a scoring fix, neither
  an obeyed instruction ([report](../evals/results/2026-11-12-redteam-v1.md),
  [rescored](../evals/results/2026-11-12-redteam-v1-rescored.md)). One model, one run.
- [x] **Dependency and image scanning in CI** ([supply chain](security/supply-chain.md)):
  osv-scanner, Trivy (repository, images, the rendered Helm chart), hadolint, actionlint, zizmor,
  SBOMs, CodeQL, Dependabot; images, actions and scanners pinned. `make scan` passes locally;
  the workflows have never run on GitHub (no repository yet).
- [x] **Load tests and performance tuning** ([load benchmarks](benchmarks/load.md)): 10 to 800
  simulated users on the main stack with a stub model, on one laptop. Up to 200 users nothing
  was saturated; at 400 the API's one process was (search p95 46 to 420 ms). Batched token
  events and two API processes brought 400 users back to unloaded latencies (chat first token
  p95 2.5 s, search p95 130 ms). At 800 users both processes, the database pools and the audit
  log's per-tenant lock are the limits. Not measured: a real model, a large corpus, many
  tenants. The sizing for 50, 200 and 1,000 employees is an extrapolation.
- [x] **Multi-tenant hardening** ([multi-tenancy](multi-tenancy.md)): tenant-prefixed Valkey
  keys, the tenant scope inside the Qdrant client, rate limits, `wm tenant`, and a test that
  sends every route another tenant's ids. Load with many tenants is not measured.
- [x] **Backup and restore; upgrades tested from v0.x** ([backup and restore](operations/backup-restore.md),
  [upgrades](operations/upgrade.md)): a restore drill into a separate stack with identical rows,
  points and files, about a minute at the demo's size; the upgrade from v0.1.0 passed 17 of 17
  checks, and rolling back by restoring the pre-upgrade backup worked.
- [x] **Helm chart** ([Kubernetes](deploy/kubernetes.md#tested-on-a-cluster)): validated with
  `helm lint`, kubeconform (Kubernetes 1.33) and its own security rules, with Helm 3.19 and
  4.0, and installed on kind clusters with Kubernetes 1.37 (Helm 3.19) and 1.34 (Helm 4.0):
  the migrate and seed hooks, `helm test`, the API end-to-end tests and 159 browser tests
  through the Ingress, the "restricted" Pod Security Standard enforced, NetworkPolicies
  enforced, an upgrade to two API replicas with every request answered, an uninstall and a
  reinstall over the kept data. Four chart problems found and fixed (among them, upgrades now
  run `wm authz sync`). Not tested: a cloud cluster, TLS, managed databases, the embedding
  server, model calls, an upgrade between two WorkMind versions.
- [x] **Docs site** (MkDocs, `make docs`, strict build in CI), with the configuration and HTTP
  API reference generated from the code. Not published: Pages needs a public repository.
- [x] **Browser end-to-end suite** in CI (`e2e/browser`): 162 tests pass on the rebuilt stack,
  including the 3 that call the model; the API end-to-end tests pass too (16, model tests
  included).
- [x] **Benchmarks for v1.0** ([v1.0 benchmarks](benchmarks/v1.0.md)): every suite at commit
  `1b62d52` with Claude Sonnet 5.5, one run each. 0 leaks in 312 retrieval checks, 118 red-team
  answers (59 attacks in `single` and `auto` mode) and 51 analytics answers; 0 violations;
  81/82 correct, faithfulness 99.5%; cross-department 14/15 with one search, 15/15 with agents;
  routing 53/56; text-to-SQL 33/36; chat time to first token p50 0.8 s (`single`), 2.6 s
  (`auto`) and $0.0071 per chat answer (`single`), $0.0114 (`auto`), over the 82 QA answers;
  the whole run $8.74. Synthetic company, small datasets, the judge from the same model family.
- [x] **A case study** ([case study](case-study.md)) of the build for the synthetic Acme Robotics:
  choices, phases, results, what went wrong, costs, lessons.
- [ ] **The v1.0 release** itself ([releasing](operations/releasing.md)): no public repository yet.
- [x] **A final review** of the merged code (2026-11-13): security, operations, CI and the
  published claims. Fixed, each security and correctness fix with a regression test: `wm tenant
  delete --keycloak` missed organizations whose name did not contain the slug (Keycloak searches
  names, not aliases), so a reused slug let the old tenant's people in; a guest could hand their
  own task to any employee with PATCH; markdown images with a line break in the alt text and
  `<image>` tags reached MCP `ask` results; with two API processes, one person ran two analytics
  queries at once and a pod could hold twice the query workers; the Helm chart's Qdrant had no API
  key and its NetworkPolicies were off by default; protocol-relative links had no host label; the
  tenant session index only grew. Operations: backups pin objects before the database snapshot (a
  delete during a backup aborted it or left a restored row without its file), refuse a Postgres
  role without `BYPASSRLS`, validate `--keep` and remove failed runs; restores name stuck meetings
  and refuse other embedding settings; PostgreSQL 18 is stated as required. CI, never run on
  GitHub: object storage owned by uid 10001 on Linux runners (seeding failed), the relevance floor
  off only for the permission gate (the guest e2e test failed without a model key), a gitleaks
  allowlist for 10 fixture findings, release-please signs off its commits. Claims: report-backed
  QA latency and cost ("By suite" tables), load figures without an artifact marked, the 421-chunk
  corpus described as measured. After the fixes, on the rebuilt stack: backend 1,408 tests, web
  1,053, API end-to-end 14 (2 model tests skipped), browser 159; one person's four simultaneous
  analytics queries now ran one at a time (4.0, 7.8 and 11.6 s; the fourth refused after waiting
  10 s), where before two finished together. The chart (0.1.5, with the Qdrant API key) passed the
  kind test again: install, seed, `helm test`, the API end-to-end tests, an upgrade to two
  replicas and an uninstall (browser suite not run). Not done: the kind test in CI; Keycloak
  refusing the first one or two sign-ins after Postgres is recreated (it recovers within seconds;
  not investigated); the load test was not run again after the analytics workers were split across
  the API processes (2 per container by default instead of 3 per process).

**Scope:**
- Security review against the OWASP Top 10 for LLM Applications. Threat model review. Red-team expansion.
- Dependency and image scanning in CI.
- Load tests (Locust). Performance tuning.
- Multi-tenant mode hardening.
- Backup and restore. Upgrade and migration guide (tested from v0.x).
- Helm chart. Complete docs site. (*The admin screens got their guide on 2026-11-13:
  [administration](user-guide/administration.md).*)
- A case study write-up.

**DoD:** v1.0 published with benchmarks (quality, latency, cost, leak rate) and a tested upgrade path.

**Launch:**
- LinkedIn post #13 — *"v1.0: what I learned building an open-source enterprise AI platform."*
- Show HN, Product Hunt.

### After v1.0 — Commercial track

- **WorkMind Cloud:** hosted multi-tenant SaaS.
- **Enterprise edition** features (§5).
- **Premium connectors:** Salesforce, ServiceNow, Zendesk, HubSpot, Jira Service Management, SAP.
- **A2A interoperability:** publish WorkMind agents to external agent ecosystems.
- Zoom / Teams / Meet meeting connectors.

---

## 4. Open-source and licensing plan

**Model: open core.** The core is open source. Enterprise features live in `ee/` under a commercial license. This is a proven model: PostHog, Langfuse and Onyx keep an `ee/` directory next to an open core; Cal.com and Grafana use AGPL; Supabase monetizes hosting of an Apache-licensed core.

| Option | Pros | Cons |
|---|---|---|
| **Apache-2.0 core + `ee/` commercial** (recommended) | Maximum adoption and stars. Enterprise legal teams approve it easily. Includes a patent grant. | Others may legally host the core commercially |
| AGPL-3.0 core + commercial license | Protects against SaaS clones. Enables dual-licensing revenue. | Some companies forbid AGPL internally. Fewer contributors. |
| Source-available (BSL / FSL / ELv2) | Strongest protection | Not OSI open source. Loses the "open source" credibility, which is the point of this project. |

**Decision: Apache-2.0 core + `ee/` commercial + DCO sign-off** ([ADR 0004](adr/0004-apache-2-open-core-and-dco.md)).

Earlier drafts proposed a CLA. With Apache-2.0 it isn't needed: the license already lets the project ship contributed code inside commercial offerings. A CLA mainly matters for relicensing to AGPL or a source-available license, which is not planned. The lighter DCO (`git commit -s`) is enough, and it lowers the barrier for contributors.
- For a solo builder seeking recognition, adoption matters most right now.
- The business moat is the hosted cloud, enterprise features, support and expertise, not the license.

**Rules:**
- The license is fixed **before the first public commit** (D1, done).
- CI checks a **DCO sign-off** on every commit in a pull request. `ee/` accepts no external contributions.
- The `ee/` boundary exists in code from day one: enterprise features are loaded only when a valid license key is present. Community code must never import from `ee/`. Built in Phase 11: [ADR 0011](adr/0011-open-core-boundary-and-license-keys.md), [docs/enterprise.md](enterprise.md).

---

## 5. Free vs. paid

**Principle:** the free edition gives an individual team everything it needs. Paid editions add what *large organizations* need for compliance, scale and administration. **Security basics are never paywalled**; a leaky free edition would destroy the brand.

| Capability | Community (free, self-hosted) | Enterprise / Cloud (paid) |
|---|---|---|
| AI assistant: hybrid RAG, citations (a reranker is not built) | ✅ | ✅ |
| Permission-aware retrieval (WorkMind-managed permissions) | ✅ | ✅ |
| Multi-agent orchestration, agents as configuration | ✅ | ✅ |
| Local models and bring-your-own API keys | ✅ | ✅ |
| Login via Keycloak (including connecting your own IdP) | ✅ | ✅ |
| Standard connectors (upload, Drive, Slack, Confluence, SharePoint/OneDrive, GitHub, web) | ✅ | ✅ |
| MCP server and client, tools, basic workflows and approvals | ✅ | ✅ |
| Meeting intelligence, basic BI agent | ✅ | ✅ |
| Eval and red-team suite | ✅ | ✅ |
| Per-document permission sync from source systems | — | ✅ |
| SCIM provisioning and IdP group sync | — | ✅ |
| Advanced audit: SIEM export, long retention, legal hold | — | ✅ |
| Data governance: retention policies, DLP rules, advanced routing policies | — | ✅ |
| Department budgets, chargeback, advanced analytics | — | ✅ |
| Premium connectors (Salesforce, ServiceNow, SAP…) | — | ✅ |
| White-labeling, multi-org administration | — | ✅ |
| High-availability deployment, priority support, SLA | — | ✅ |
| Fully managed hosting (WorkMind Cloud) | — | ✅ |

---

## 6. Release process

- **Versioning:** SemVer. Pre-1.0, a minor version is a phase (0.1, 0.2…). Patch versions are fixes.
- **Flow:** Conventional Commits → release-please opens a release PR → merge tags `vX.Y.Z` → GitHub Release.
- **Release contents:**
  - Changelog and upgrade notes.
  - Multi-arch Docker images (amd64 + arm64) on GitHub Container Registry.
  - The eval report for that version.
- **Migrations** always go forward through Alembic. Breaking changes are called out in release notes.
- **Every release also gets:** a LinkedIn post, a devlog entry in `docs/devlog/`, and a refreshed demo deployment. (*As built:* `docs/devlog/` has drafts of posts 1 to 6; posts 7 to 13 are not written, and there is no demo deployment.)

---

## 7. LinkedIn and community playbook

**Cadence:**
- One **milestone post per phase** (13 total).
- One smaller "lesson" post in between: a bug you fixed, a benchmark, a design decision.
- That works out to roughly one post a week.

**Structure of a strong post:**
1. **Hook:** a problem, a surprising insight, or a number. *Not* "I built X."
2. **What I built,** in 2–3 lines.
3. **One visual:** a diagram, a demo GIF, or a results chart.
4. **One lesson or measured number,** including what didn't work (honesty builds credibility).
5. **Link to the repo.** Many creators put links in the first comment.
6. **A question** that invites comments.

**Other channels:**
- Keep drafts in `docs/devlog/` and reuse them as blog posts (dev.to, Medium, personal site).
- Cross-post to X.
- Use Reddit only where self-promotion rules allow.
- Use Hacker News only for the two big launches (v0.4 and v1.0).

**Growing stars:**
- A great README with a GIF and a one-command quickstart.
- A live demo with role logins.
- `good first issue` labels, GitHub Discussions.
- Responsive issue handling.
- Mention the open-source projects you build on; maintainers sometimes reshare.

**Repo visibility:** go **public from Phase 0**, clearly labeled *pre-alpha*. Building in public means people can see real commits behind every post. The big **launches** wait for v0.4 (first usable product) and v1.0, because first impressions from those audiences count.

---

## 8. Checklist before going public

- [ ] **Name check** (deferred until the end-to-end build, D2): trademark databases, domains, GitHub org, PyPI, npm. The name lives only in config, so a rename stays cheap. Keep in mind that LinkedIn posts build recognition around whatever name they use.
- [ ] **Employment check:** if you are employed, read your contract's IP / side-project / outside-business clauses before commercializing.
- [x] License chosen (Apache-2.0). `ee/` boundary created. DCO check in CI (D1).
- [x] `SECURITY.md` (private vulnerability reporting), `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`, issue and PR templates.
- [ ] Enable **private vulnerability reporting** in the GitHub repo settings (Security tab) once the repo exists.
- [x] gitleaks in pre-commit and CI. **No real company data anywhere**: synthetic data only, clearly fictional.
- [ ] GitHub organization created. Branch protection on `main`. Required CI checks.

---

## 9. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Crowded market (Onyx, RAGFlow, Dify, AnythingLLM, Open WebUI…) | Compete on the wedge: provable permission safety, published measurements, local-first. Don't compete on feature count. |
| Scope and time overrun | Phases that ship on their own. Never start a phase before the previous one is tagged. |
| Local model quality on tool calling | Per-provider eval tables. Hybrid policy (local for sensitive data, paid for the rest). |
| Demo hosting cost | Paid API with a strict budget for the public demo. Local models in self-hosted setups. |
| Maintenance load after launch | Issue templates, scoped contributions, clear "not planned" policy, sustainable cadence |
| Burnout | Fixed weekly hours. Celebrate each release. Lesson posts keep momentum without big features. |
