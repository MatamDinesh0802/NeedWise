# WorkMind AI — Roadmap, Releases and Go-to-Market

> **Status:** Draft v0.2 · **Phase 0 complete** · **Last updated:** 2026-11-02
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
| 1 | v0.1 | Identity, tenancy and permissions | 2 wk | Login + permission model demo |
| 2 | v0.2 | Knowledge ingestion | 2 wk | 500-document corpus ingested, live status |
| 3 | v0.3 | Retrieval engine + evaluation harness | 2–3 wk | First published eval numbers |
| 4 | **v0.4** | **AI Assistant — first usable product** | 2 wk | **Public launch** (HN, Reddit, LinkedIn video) |
| 5 | v0.5 | Multi-agent orchestration | 2–3 wk | "Do you need multi-agent? I measured it." |
| 6 | v0.6 | Tools and MCP | 2 wk | WorkMind inside Claude Desktop / Cursor |
| 7 | v0.7 | Connectors and sync | 3 wk | Google Drive + Slack live sync |
| 8 | v0.8 | Workflow automation and approvals | 2–3 wk | Human-in-the-loop demo |
| 9 | v0.9 | Meeting intelligence | 2 wk | Recording → decisions and action items |
| 10 | v0.10 | Business intelligence agent | 3 wk | Question → chart + SQL + explanation |
| 11 | v0.11 | Admin, governance and usage analytics | 2 wk | New agent created without code |
| 12 | **v1.0** | **Production hardening** | 2–3 wk | **v1.0 launch** (HN, Product Hunt) |
| — | after 1.0 | Commercial track | ongoing | WorkMind Cloud + Enterprise edition |

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

**LinkedIn post #1 — "I'm building an open-source enterprise AI workspace, in public."**
- The problem: company knowledge is scattered, and AI assistants leak permissions.
- The architecture diagram and the 8 design principles.
- Ask: *"What would make you trust an AI assistant with your company's documents?"*

### Phase 1 — Identity, tenancy and permissions (v0.1) 🚧 In progress

**Goal:** real login, real organizations, and a permission model that everything else builds on.

**Progress:**
- [x] Slice 1: tenant data model with forced Row-Level Security, plus isolation tests
- [x] Slice 2: Keycloak realm as code, `make seed`
- [x] Slice 3: login/logout (BFF), sessions, back-channel logout, `/api/v1/me`, end-to-end tests
- [ ] Slice 4: web sign-in and user menu
- [x] Slice 5: OpenFGA model, outbox sync, reconcile, authz matrix tests
- [ ] Slice 6: admin API + UI, audit log viewer
- [ ] Slice 7: personal access tokens

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

**LinkedIn post #2 — "Most RAG demos skip the hardest enterprise problem: who is allowed to see what."**
- Show the login flow diagram and the OpenFGA model.
- Explain the "two locks" idea.

### Phase 2 — Knowledge ingestion (v0.2)

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

**LinkedIn post #3 — "What actually happens between 'upload PDF' and 'AI answers'."**
- Pipeline diagram.
- Lessons on chunking tables and on versioning.
- Real throughput numbers.

### Phase 3 — Retrieval engine and evaluation harness (v0.3)

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

### Phase 4 — AI Assistant: first usable product (v0.4) — PUBLIC LAUNCH

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

### Phase 5 — Multi-agent orchestration (v0.5)

**Goal:** complex, cross-department requests answered by cooperating agents, with proof that it helps.

**Scope:**
- LangGraph run graph, supervisor planner (structured output), fast-path router.
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

### Phase 6 — Tools and MCP (v0.6)

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

### Phase 7 — Connectors and sync (v0.7)

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

### Phase 8 — Workflow automation and approvals (v0.8)

**Goal:** WorkMind does work, safely, with humans in control.

**Scope:**
- Approval service, approvals inbox, inline approval cards (approve / edit / reject).
- LangGraph interrupts for agent actions.
- Tasks module. SMTP email adapter. `send_email` with approval.
- Request routing to department queues.
- Declarative workflows (YAML) with manual / schedule / event triggers.
- Scheduled weekly report. Notifications. Expiry and escalation. Operations agent.

**DoD:** *"Prepare the weekly ops report and email it to leadership"* → draft → approval (with an edit) → sent → fully audited.

**LinkedIn post #9 — "Designing human-in-the-loop for AI agents: when should the AI ask permission?"**

### Phase 9 — Meeting intelligence (v0.9)

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

**LinkedIn post #10 — "From a 30-minute meeting to tracked commitments, fully self-hosted."**

### Phase 10 — Business intelligence agent (v0.10)

**Goal:** analytical questions answered with charts, explanations and verifiable SQL.

**Scope:**
- Data source connectors (Postgres/MySQL; CSV/XLSX → DuckDB).
- Semantic layer YAML. Text-to-SQL via the semantic layer.
- sqlglot validation, read-only execution with limits, row-level policies.
- Chart specs + rendering. Narrative with number verification.
- Insights page. Analytics agent.
- Text-to-SQL eval set (execution accuracy).

**DoD:** *"Monthly revenue trend this year and the top 3 drivers"* → chart + explanation + SQL + data link. Execution accuracy is published.

**LinkedIn post #11 — "Letting an LLM write SQL against company data, safely."**

### Phase 11 — Admin, governance and usage analytics (v0.11)

**Goal:** organizations can run WorkMind themselves: configure, control and account for it.

**Scope:**
- Agent configuration UI: prompt, tools, scopes, model, limits, versions, test console.
- Model and provider management: bring-your-own keys, routing policies.
- Usage and cost analytics per department / user / agent / model. Quotas and budgets.
- Audit log explorer. Retention policies.
- Knowledge governance: owners, review reminders, "official" approval flow for documents.
- Support, Sales and Marketing agents created **via config only**.

**DoD:** an admin creates a new "Legal agent" from the UI without code, and it routes correctly in the routing eval.

**LinkedIn post #12 — "Adding a new AI agent to a company without writing code."**

### Phase 12 — Production hardening (v1.0)

**Goal:** a release companies can trust in production.

**Scope:**
- Security review against the OWASP Top 10 for LLM Applications. Threat model review. Red-team expansion.
- Dependency and image scanning in CI.
- Load tests (Locust). Performance tuning.
- Multi-tenant mode hardening.
- Backup and restore. Upgrade and migration guide (tested from v0.x).
- Helm chart. Complete docs site.
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
- The `ee/` boundary exists in code from day one: enterprise features are loaded only when a valid license key is present. Community code must never import from `ee/`.

---

## 5. Free vs. paid

**Principle:** the free edition gives an individual team everything it needs. Paid editions add what *large organizations* need for compliance, scale and administration. **Security basics are never paywalled**; a leaky free edition would destroy the brand.

| Capability | Community (free, self-hosted) | Enterprise / Cloud (paid) |
|---|---|---|
| AI assistant: hybrid RAG, reranking, citations | ✅ | ✅ |
| Permission-aware retrieval (WorkMind-managed permissions) | ✅ | ✅ |
| Multi-agent orchestration, agents as configuration | ✅ | ✅ |
| Local models and bring-your-own API keys | ✅ | ✅ |
| Login via Keycloak (including connecting your own IdP) | ✅ | ✅ |
| Standard connectors (upload, Drive, Slack, Confluence, GitHub, web) | ✅ | ✅ |
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
- **Every release also gets:** a LinkedIn post, a devlog entry in `docs/devlog/`, and a refreshed demo deployment.

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
