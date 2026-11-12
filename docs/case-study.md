# Case study: WorkMind at Acme Robotics

> **Acme Robotics is not a real company.** It is a fictional 180-person warehouse robotics
> company made up for this project. Its 20 modelled employees, its documents, its invoices and
> sales pipeline, its meetings and its Slack messages are synthetic, written from a fact plan
> (`seed/acme/`) so that every test question has a known answer. No real organization has
> deployed WorkMind, and nothing here describes real users. The "deployment" is the demo tenant
> on one laptop; every result below was measured on it.

This page tells the story of the build as if Acme were the customer: what it needed, what was
chosen and why, what each phase delivered, what was measured, what went wrong, what it costs
and what was learned. The numbers are from the [v1.0 benchmarks](benchmarks/v1.0.md) and the
reports they link to.

## The problem

Acme's knowledge is spread over policies, handbooks, spreadsheets, support tickets, sales
proposals, Slack channels and meeting notes. Some of it disagrees with itself on purpose: a 2025
and a 2026 leave policy, a Slack thread claiming 20 weeks of parental leave where the policy
says 16. Some of it is restricted: salary bands, a performance review, payroll, an acquisition
memo, board material. Finance and sales keep tabular data (invoices, pipeline) that people
want to ask questions about.

What an assistant for Acme has to do:

- **Answer from Acme's own documents, with citations**, and say so when it does not know.
- **Show each person only what they may open.** An engineer asking for salary bands gets "I
  couldn't find this", not a hint that the document exists. The IT administrator manages the
  system but is not a reader of HR's restricted files. The external contractor sees one
  collection.
- **Not obey text inside documents.** A vendor's release notes, a customer in a meeting or an
  outside Slack member can write instructions; they are data.
- **Act only with a person's approval** when something leaves the system (an email, a task for
  someone else).
- **Keep restricted data local** if the company decides so, and show what the models cost.
- **Run on Acme's own machines**, with any model provider or a local model.

These were written down as tests before most of the code: canary tokens in every restricted
document (any canary in an unauthorized person's context or answer is a leak, no judgement
needed), a permission grid of people by collections, and a red team.

## What was deployed

One Docker Compose stack on a laptop (Apple M5, 24 GB): Caddy in front of the Next.js web app
and the FastAPI API (two processes), a Celery worker and scheduler, PostgreSQL, Qdrant, Valkey,
Keycloak, OpenFGA and Mailpit; the embedding model (EmbeddingGemma-300M) on the laptop's GPU;
Claude Sonnet 5.5 through Anthropic's API for answers, planning, agents and extraction. The
Acme tenant holds 20 people with 9 demo logins, 14 collections (15 during the v1.0 runs: the
load test had added an empty one), 47 seed documents (364 chunks) plus 2 meeting transcripts
that tests indexed (49 documents, 421 chunks in the measured index), an analytics dataset, 9
department agents (one of them, Legal, created in the browser without code) and an Enterprise
development license.

## Architecture choices, and why

| Choice | Why | Record |
|---|---|---|
| A modular monolith: one API, workers and a scheduler from one codebase | Many capabilities, a very small team; services would multiply deployment and consistency work long before anything needs to scale on its own | [ADR 0002](adr/0002-modular-monolith.md) |
| Sign-in through the backend (BFF) with Keycloak; tokens never in the browser | Enterprise SSO and MFA without exposing tokens to scripts; the same identity for the web app, API clients and MCP clients | [ADR 0003](adr/0003-bff-authentication-with-keycloak.md) |
| Forced row-level security, with the application's database role unable to bypass it | A forgotten tenant filter returns nothing instead of another company's rows | [ADR 0005](adr/0005-postgres-roles-and-rls.md) |
| Two locks on retrieval: an access list on every chunk filters the search, then OpenFGA re-checks every source | Filtering after search hurts recall and is easy to forget; checking every chunk at query time is too slow | [ADR 0006](adr/0006-two-lock-permission-aware-retrieval.md) |
| Claude by default, local embeddings, swappable adapters, lightweight parsing | A hosted model gives far better answers per GB than a local one on a 24 GB laptop; a small local embedding model keeps document text local while indexing | [ADR 0007](adr/0007-model-providers-and-lightweight-parsing.md) |
| Hybrid search with tuned fusion (RRF k=2, BM25 weight 0.5); routing of restricted data by policy | Measured: textbook fusion (k=60) was worse than either input alone | [ADR 0008](adr/0008-retrieval-fusion-and-sensitivity-routing.md) |
| Department agents in plain asyncio; one search stays the default | The plan is shallow and fixed; agents won only on questions spanning three areas, at several times the latency and cost | [ADR 0009](adr/0009-orchestration-without-a-graph-framework.md) |
| Analytics through a semantic layer, SQL checked and rewritten with sqlglot, run in sandboxed DuckDB | Permissions and row policies decided in code whatever SQL the model writes; the model reaches nothing else | [ADR 0010](adr/0010-analytics-semantic-layer-duckdb-sqlglot.md) |
| Apache-2.0 core with an `ee/` directory behind signed license keys | Adoption first; security basics never paywalled; the core runs without any Enterprise code | [ADR 0004](adr/0004-apache-2-open-core-and-dco.md), [ADR 0011](adr/0011-open-core-boundary-and-license-keys.md) |

Three planned choices were dropped once the work showed they were not needed: LiteLLM and
Docling (replaced by thin adapters and lightweight parsers, ADR 0007) and a LangGraph run graph
(replaced by about 700 lines of asyncio, ADR 0009).

## What each phase delivered

The [roadmap](ROADMAP.md) has the detail. Only v0.1.0 was tagged; Phases 2 to 11 were finished
on `main` without a release ([CHANGELOG](../CHANGELOG.md)).

| Phase | Delivered | Measured, or still open |
|---|---|---|
| 0 Foundation | One-command stack, CI, the Acme company definition | |
| 1 Identity and permissions | Keycloak sign-in, tenants with forced RLS, departments and nested groups, OpenFGA with an outbox, access tokens, a hash-chained audit log | A permission grid of 6 people x 7 collections as 42 tests |
| 2 Ingestion | Upload of PDF, Office, Markdown, HTML, CSV and Slack exports; structure-aware chunking; sensitivity labels; versions; local embeddings | 34 documents became 294 chunks; re-indexing them took 8.6 s. The planned 500-document corpus was not built (47 today) |
| 3 Retrieval and evaluation | Hybrid search, both locks, citations, abstention, sensitivity routing, `wm eval`, the CI permission gate | 81/82 correct, 0 leaks. Deferred: a reranker (no headroom measured), the local-versus-hosted model comparison, 200 QA questions |
| 4 Assistant | Streaming chat with citation chips, follow-ups, feedback, usage per message, "view as" | Not done: the demo video and public demo |
| 5 Agents | Planner, fast path, department agents, the `auto` mode | Agents win 1 of 15 cross-department questions; `single` stays the default |
| 6 Tools and MCP | Governed tools, the research agent, an MCP client and WorkMind as an MCP server | |
| 7 Connectors | Drive, Slack, GitHub and a web crawler with a sync engine | Not verified against a live Drive or Slack workspace |
| 8 Workflows and approvals | Approvals, tasks, queues, email with approval, workflows with cron and document triggers | The weekly ops report flow ran live. Not built: escalation, email or Slack notifications, request routing |
| 9 Meetings | Recordings and transcripts to decisions and action items with timestamps, tasks, indexed transcripts | A 23-minute recording transcribed in 43 to 47 s on the laptop's CPU |
| 10 BI agent | CSV/XLSX in DuckDB, semantic layer, SQL guard with row policies, charts, checked numbers, Insights | 33/36 execution accuracy, 0 leaks. Not built: Postgres and MySQL sources |
| 11 Admin, governance, usage | Agents as configuration, models and keys per organization, usage and budgets, document owners and official status, the audit explorer, four Enterprise features | A Legal agent made in the browser routes 15/15 legal questions |
| 12 Hardening | Security review and a 59-attack red team, supply-chain scans, multi-tenant hardening, backup and restore, upgrades from v0.1.0, a Helm chart, the docs site, browser tests, load tests | 0 leaks; restore drill about a minute; upgrade 17/17 checks; load tuned to 400 users; the Helm chart installed and upgraded on kind (four chart problems fixed). Not done: a cloud cluster, the v1.0 release |

## Results

From the [v1.0 benchmarks](benchmarks/v1.0.md) (commit `1b62d52`, 2026-11-12, Claude Sonnet 5.5,
one run each):

| | Result |
|---|---|
| Permission leaks | 0 in 312 retrieval checks, 118 red-team answers on four surfaces, 51 analytics answers |
| Red-team violations | 0 (2 in the previous run of the same attacks, neither an obeyed instruction) |
| Answers with every required fact | 81/82; faithfulness 99.5% (judged by the same model family); abstention 6/6 |
| Retrieval, hybrid | Doc Recall@5 100%, Evidence@1 90.2% |
| Cross-department questions | 14/15 with one search, 15/15 with agents |
| Routing | 53/56 exact plans |
| Text-to-SQL | 33/36 |
| Meetings | 10/10 decisions, 13/13 action items with due dates over 3 extractions |
| Chat time to first token, p50 / p95 | 0.8 s / 1.5 s (`single`), 2.6 s / 6.0 s (`auto`) |
| Under load, stub model | Up to 400 simulated users at unloaded chat latency after tuning; degrades without errors at 800 |

What the numbers do not show: a real company's documents, questions and attackers, other
models, a long run in production.

## What went wrong, and was fixed

The evaluation, the reviews and the load test found real defects. Each fix below has a
regression test or a re-run report.

**Found by the evaluation**

- **The two locks disagreed for auditors.** The index treated them as organization members, the
  authorization model did not; lock 2 dropped their results. A test now compares both locks for
  every person and collection.
- **Textbook hybrid search was worse than either input** (Evidence@1 69.5% against 72.0% for
  BM25 and 90.2% for dense). Tuned fusion: 92.7%.
- **The answer prompt contradicted itself** (hints about where an answer might be, and no
  speculation). Removing the hints raised faithfulness from 96.5% to 99.8%. Switching to Sonnet
  5.5 later surfaced a trust-level note added to ordinary answers (97.2%); scoping it to chat and
  ticket sources gave 99.4%.
- **Streams broke after 5 quiet seconds**: the Valkey client's socket timeout cut a blocking read
  while a slow agent worked.
- **Structured outputs dropped fields named `title`**, so every BI answer with a chart failed to
  compose; and **the BI agent replaced the model's main query with the charted one** (3 of 36
  misses).
- **A new agent changed an unrelated plan**: with Legal listed, "What is Project Heron?" went to
  no agent at all; the planner's rule for answering directly was reworded.
- **Indexing renamed meeting transcripts**, a race an integration test hit one run in three.

**Found in development** (the devlog drafts tell these): the sensitivity scan labelled the leave
policy restricted because it mentions medical certificates (it now looks for data, not topics);
the embedding server rejected a 541-token chunk because its context is shared across 4 slots;
"view as" signed the previous person back in through Keycloak's live session; PostgreSQL's
foreign-key checks ignore row-level security, so links to another tenant's rows needed
composite keys.

**Found by the reviews**

- *Analytics (Phase 10):* with a row policy as a subquery, DuckDB could run the query's own
  failing expressions on hidden rows, so a sales rep could read other owners' invoice lines one
  error message at a time; `WITH RECURSIVE x AS (SELECT * FROM x)` read any table or catalog
  view named `x`; inner joins dropped orphan rows and double counted repeated keys. Row policies
  are now copied into temporary tables, the guard binds CTEs as DuckDB does, joins are left
  joins with checked keys, and queries run in worker processes with memory and time limits.
- *Phase 11:* an editor could replace an official document's content and keep its official
  status; official-status requests went to administrators who could not open the collection;
  tool loops were checked against budgets once instead of every turn; a budget crossed
  mid-answer showed only "This answer failed".
- *Security review against the OWASP Top 10 for LLM applications (Phase 12):* 11 fixes, among
  them the web crawler following redirects to the cloud metadata address, answers carrying
  remote images that could exfiltrate data, citation markers written inside documents, unescaped
  meeting transcripts, MCP results and conversation summaries, guests able to send company email,
  unbounded answers in progress per person and unbounded research tool calls
  ([review](security/owasp-llm-top10.md)).

**Found by the load test:** at 400 simulated users a profile showed the API's one process
spending 27% of its event loop writing and reading one event per streamed word (the profile's
output was not kept), and its database connections reached the pool's 20. Batched
token events, one Valkey round trip per event and two API processes brought 400 users back to
unloaded latencies ([load benchmarks](benchmarks/load.md)).

**Found in the evaluation itself:** an empty gold result counted as found; four reports named
a commit that did not contain the code they ran (reports now mark uncommitted changes); near
misses of an agent that did not exist were counted as kept away; an abstention that listed
related material, as the prompt asks, was scored as a copied prompt; and the meetings suite
tried to transcribe a meeting that has no recording.

## Costs

- **Per answer**, with Claude Sonnet 5.5 at $2 / $10 per million tokens: $0.0071 for a chat
  answer in `single` mode, $0.0114 in `auto`, $0.037 for a cross-department answer from agents,
  $0.026 for a BI answer, $0.027 per meeting extracted.
- **A month for 200 employees**, estimated from those costs and assumed usage (half the staff
  asking 4 questions a working day, 10 BI users, 130 meetings): about $80 in `single` mode and
  $140 in `auto`, or $0.40 to $0.70 per employee; about $480 to $760 in a heavier variant
  ([assumptions](benchmarks/v1.0.md#a-200-person-company-per-month-estimate)). Plus a machine of
  about 4 vCPU and 8 GB for the stack, by extrapolation from the load test, and the embedding
  server.
- **Choosing the model** was a cost decision: on the same questions Sonnet 5.5 matched Opus 5.5
  on correctness (81/82) at 40% of the cost per answer, and became the default.
- **The v1.0 benchmark** made 1,041 model calls for $8.74, the judge included.
- **Not known:** the model spend of the whole build. Usage events exist since Phase 11; earlier
  evaluation runs were not metered.

## Lessons

- **Measure the defaults you are told to use.** Textbook hybrid search lost to both of its
  inputs, and multi-agent orchestration helped on one question in fifteen at four to five times the cost.
  Both would have shipped as defaults without the measurements.
- **Make the safety property binary.** Canary tokens and forbidden strings turn "did it leak?"
  into a string match, which can gate every change. Zero leaks still only means zero for the
  attacks that were written.
- **Test security with a model that obeys.** Tests in which a scripted model does exactly what an
  injected instruction asks pass only when code, not the prompt, stops the attack. Most of the
  review's fixes were found that way.
- **The live evaluation finds what unit tests do not:** the lock disagreement, the stream timeout,
  the dropped `title` field and the planner rule were all invisible to tests with scripted models.
- **A report must name the code it ran**, and its scoring needs tests of its own: several
  "results" were scoring errors.
- **Small sets move.** With 82 or 15 questions and a non-deterministic model, single items change
  between runs; only differences that repeat are findings.
- **The first bottleneck was our own code**, the per-token event path, not the database or the
  vector store.

What is still missing before a real company should use it: a tagged v1.0 release and a public
repository, an install on a cloud or production Kubernetes cluster (the chart was only tested
on kind), live verification of the Drive and Slack
connectors, an external penetration test, a judge from another model family, measurements with
local models, and a corpus and question set much larger than Acme's.

WorkMind was built by one maintainer with Claude as a coding assistant: every commit carries a
Claude co-author line except one merge commit (bb4dc95).
