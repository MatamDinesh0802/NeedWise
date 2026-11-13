# WorkMind AI

**The open-source enterprise AI assistant that is permission-safe, cited, measured and self-hosted.**

Employees ask questions in plain language and get answers grounded in company knowledge, with
citations, drawn only from sources they are allowed to see. Specialized agents (HR, Finance,
Engineering…) handle department work. Actions that leave the system need human approval.

![Alice, an HR manager, gets the L3 salary band with a citation she can open; Bob, an engineer, asks the same and is told the sources available to him don't contain it](docs/media/demo.gif)

The [full walkthrough](docs/media/demo.mp4) (1 min 44 s, recorded with Claude Sonnet 5.5 on the
demo company; waits for the model are sped up): a cited answer and its source, the same question without access, three department agents on one
question, an email approved after an edit, a meeting's clickable timestamps, a chart with its
SQL, and an administrator adding an agent.

> **Status: v1.0, pending the release tag.** All 13 phases of the [roadmap](docs/ROADMAP.md)
> are built; a last end-to-end pass and the release remain. Not done yet: a public repository
> (so the release workflows have never run), a test on a cloud Kubernetes cluster, and checking
> Google Drive and Slack sync against real workspaces. It has been run only on the fictional demo
> company, not on real company data.

## Quick start

You need Docker with the Compose plugin (the `docker compose` command), `git` and `make`. These
ports on `127.0.0.1` must be free: 8480, 8400, 3400, 8401, 5432, 6379, 6333 and 8025
([to move one](docs/getting-started/configuration.md#ports-passwords-and-compose)).

```bash
git clone <repo-url> workmind && cd workmind
cp .env.example .env    # then add a model to .env (below), e.g. ANTHROPIC_API_KEY=sk-ant-...
make quickstart         # builds and starts the stack, waits until it is healthy, seeds the demo company
```

Open **http://localhost:8480**, sign in as `alice@acme.test` with the password `workmind-demo`,
and ask *"What is the salary band for an L3 engineer?"* The answer cites Salary Bands 2026:
click the citation to open it.

| Model | Lines in `.env` |
|---|---|
| Claude (the default) | `ANTHROPIC_API_KEY=sk-ant-...` |
| Fully local (Ollama, vLLM, llama.cpp) | `LLM_PROVIDER=openai_compatible`, `LLM_BASE_URL=http://host.docker.internal:11434/v1`, `LLM_MODEL=qwen3:14b` |

No key yet? `make quickstart` works without one: search, the Knowledge Hub and the admin pages
work, and the assistant says that no language model is set up. Add a key to `.env` later and run
`make up`. Every model role can be set separately, and restricted documents can be kept on a
local model ([models](docs/models.md), [local models](docs/getting-started/local-models.md)).

**How long it takes.** Measured on 2026-11-13 from fresh clones, on an Apple M5 laptop (Docker
Desktop: 8 CPUs, 16 GB) that was running seven other build-and-test jobs at the same time:

| From `git clone` | First run (empty build cache) | Second run (build cache kept) |
|---|---|---|
| Every service healthy | 1 min 54 s | 1 min 24 s |
| Demo company seeded (47 documents indexed) | 3 min 3 s | 2 min 40 s |
| First cited answer in the browser | about 3 min 50 s: the key added after `make quickstart`, then `make up` (41 s) | about 2 min 45 s: the key in `.env` first |

The service images (0.9 GB compressed: Postgres, Keycloak, Qdrant, the embedding server…) were
already on that machine, so a first run elsewhere also waits for that download. The answer
times add up the measured steps (sign-in about 2 s, the answer 2.4 s); a person reading and
typing along was not timed. Details:
[Quickstart › How long it takes](docs/getting-started/quickstart.md#how-long-it-takes).

> **On a Mac?** `make embeddings` runs the embedding model on the Apple GPU instead
> (`brew install llama.cpp`; set `EMBEDDING_BASE_URL=http://host.docker.internal:8091/v1`).
> Both produce the same vectors, so you can switch without re-indexing.
>
> **Memory:** the core stack needs about 3 GB. Docker Desktop's memory limit can stay
> around 8 GB. Run local LLMs in Ollama **natively** (Docker cannot use the Apple GPU), and
> keep the rest of your RAM for them.

| URL | What |
|---|---|
| http://localhost:8480 | The app: UI, API and login on one origin, via Caddy |
| http://localhost:8480/api/docs | Interactive API docs |
| http://localhost:8480/auth | Keycloak (admin console login: `admin` / `admin`, dev only) |
| http://localhost:8025 | Mailpit: every email the demo sends lands here; nothing is delivered |

`make status` prints component health, `make down` stops the stack (data is kept), `make reset`
deletes it, and `make` lists every command.

## Demo logins and what to try first

Every demo login uses the password `workmind-demo` (development only). **View as…** in the user
menu switches between them.

| Sign in as | Who | Try |
|---|---|---|
| `alice@acme.test` | HR manager | *"What is the salary band for an L3 engineer?"*: a cited answer from a restricted document |
| `bob@acme.test` | Software engineer | The same question: *"I couldn't find this in the sources available to you"*, with no hint that the document exists |
| `olivia.grant@acme.test` | Head of operations | **Research & act**: *"Prepare this week's operations report and email it to leadership@acme.test."* The email waits on an approval card; edit it, approve it, and find it in Mailpit |
| `carol@acme.test` | Finance analyst | **Insights**: *"Monthly revenue trend this year and the top 3 drivers"*: a chart, the explanation and the SQL |
| `dan@acme.test` | Account executive | **Insights**: *"Show revenue by customer."* Dan only ever sees his own 13 customers, whatever SQL the model writes |
| `erin@acme.test` | IT administrator | **Admin**: add an agent in Agents, try it in its test console; models, budgets, the audit log |
| `mallory@acme.test` | External contractor | Sees one collection: the red-team user |

Also: `priya.raman@acme.test` (CEO) and `hannah.schmidt@acme.test` (CFO). More to try:

- **Department agents.** Set `ASSISTANT_MODE=auto` in `.env`, run `make up`, and ask as Bob:
  *"For my on-call week: how do I restart the fleet controller, what P1 response time do
  customers get, and how close may I get to a moving AMR-500?"* Engineering, Support and
  Operations agents each answer their part ([ADR 0009](docs/adr/0009-orchestration-without-a-graph-framework.md)).
- **Meetings.** As Olivia, upload `seed/acme/meetings/weekly-ops-sync-2026-11-02.m4a` to
  Operations SOPs in **Meetings**: local speech-to-text (the first upload downloads the speech
  model), then decisions and action items that link to the moment they were said.
- **Conflicting sources.** *"How many weeks of parental leave do we get?"*: an older policy and
  a Slack thread disagree with the official 2026 policy; the answer prefers the official one and
  says so.

The demo company and every login: [seed/README.md](seed/README.md) and
[Quickstart](docs/getting-started/quickstart.md#demo-logins).

## Documentation

The documentation lives in [`docs/`](docs/index.md) and builds into a site with
`make docs-serve` (http://127.0.0.1:8495; needs [uv](https://docs.astral.sh/uv/)). It is not
hosted anywhere yet. Good places to start:

| You want to | Read |
|---|---|
| Install and try it | [Quickstart](docs/getting-started/quickstart.md), [configuration](docs/getting-started/configuration.md), [local models](docs/getting-started/local-models.md) |
| Use it | [User guide](docs/user-guide/assistant.md) |
| Administer it | [Agents](docs/agents.md), [models](docs/models.md), [usage and budgets](docs/usage.md), [governance](docs/governance.md) |
| Run it for real | [Kubernetes](docs/deploy/kubernetes.md), [backup and restore](docs/operations/backup-restore.md), [upgrades](docs/operations/upgrade.md) |
| Understand it | [Architecture](docs/ARCHITECTURE.md), [decision records](docs/adr/), [roadmap](docs/ROADMAP.md) |

## Benchmarks (v1.0)

Measured on 2026-11-12 at commit `1b62d52`, on one laptop, against the fictional company Acme
Robotics (49 documents: the 47 the seed indexes and 2 meeting transcripts; 82 questions, 59
attacks), with Claude Sonnet 5.5 in every role. One run per configuration: small datasets,
non-deterministic models. Method, every report and the limitations:
[docs/benchmarks/v1.0.md](docs/benchmarks/v1.0.md).

| | Result |
|---|---|
| Permission leaks | **0** in 312 retrieval checks, 118 red-team answers on four surfaces, 51 analytics answers |
| Answers with every required fact / faithfulness / abstention | 81/82 / 99.5% / 6/6 |
| Cross-department questions: one search / department agents | 14/15 / 15/15 |
| Routing to department agents / text-to-SQL execution accuracy | 53/56 / 33/36 |
| Time to first token, p50 / p95, QA questions (one search; `auto` mode) | 0.8 / 1.5 s; 2.6 / 6.0 s |
| Cost per answer, QA questions (one search; `auto`); BI agent | $0.0071; $0.0114; $0.026 |
| Load, stub model | up to 400 simulated users at unloaded chat latency on one laptop ([load](docs/benchmarks/load.md)) |

A [case study](docs/case-study.md) tells how it was built for Acme, what went wrong and what it
costs.

## What works today

- **Ask questions, get cited answers.** Streaming chat over company documents: every claim
  cites a source you can open, follow-up questions are understood, and the assistant says
  so when it doesn't know.
- **Permission-safe by construction.** Search filters every chunk by its access list (lock 1),
  then re-checks each source with OpenFGA (lock 2). The answer model only ever sees what the
  asking person may read. A red-team suite with canary tokens must show **zero leaks** in CI.
- **Measured.** `wm eval` asks every question as a specific employee, on every surface: 0 leaks
  in 59 attacks, 81/82 correct, 99.5% faithfulness, 0.8 s to the first token, $0.007 per answer
  with Claude Sonnet 5.5 ([benchmarks](#benchmarks-v10), [evaluation](evals/README.md)).
- **Inside your IDE or desktop assistant, permissions intact.** WorkMind is an MCP server:
  connect Claude Code, Cursor or Claude Desktop with a personal access token ([docs/mcp.md](docs/mcp.md)).
- **Department agents when a question spans teams**, measured against a single search, which
  stays the default because it wins on everything else ([ADR 0009](docs/adr/0009-orchestration-without-a-graph-framework.md)).
  Agents are configuration: an administrator adds one in the UI (collections, read-only tools,
  model, limits), tries it on a draft in a test console, and can restore any earlier version
  ([docs/agents.md](docs/agents.md)). A Legal agent added that way, with no code, gets 15 of 15
  legal questions in the routing evaluation and none it should not (53/56 overall with Claude
  Sonnet 5.5).
- **Your models, per organization.** Admin › Models adds providers (Anthropic or any
  OpenAI-compatible server, hosted or local) with encrypted, write-only keys, a catalog with
  prices, which model serves each role and each agent, and how sensitive data sent to external
  models may be ([docs/models.md](docs/models.md)).
- **Usage, costs and budgets.** Every model call is recorded (tokens and cost, never the text)
  for the person, department, agent and feature it served; managers see their departments,
  everyone their own usage. Organization budgets and personal caps warn or stop model use
  ([docs/usage.md](docs/usage.md)).
- **Restricted data stays local** if you want: per-company policy routes restricted sources
  to a local model, or leaves them out and says so.
- **Knowledge Hub:** upload PDF, Word, Excel, PowerPoint, Markdown, HTML, CSV or Slack exports;
  watch them get indexed; see exactly which chunks the assistant reads.
- **Meetings become commitments.** Upload a recording or transcript: local speech-to-text (a
  23-minute meeting in under a minute on a laptop CPU), decisions and action items that each
  link to the moment they were said, one click to a tracked task ([docs/meetings.md](docs/meetings.md)).
- **Questions about company data, answered with a chart, the SQL and checked numbers.** Upload
  CSV or Excel tables and describe them once in a semantic layer; the BI agent queries them
  as the person asking (an account executive sees only his accounts, whatever SQL the model
  writes), in a read-only DuckDB sandbox, and checks every number in its answer against the
  query results ([docs/analytics.md](docs/analytics.md)). On a 51-question text-to-SQL eval
  with Claude Sonnet 5.5: 33/36 answers return the gold result, 0 permission leaks, 6.8 s
  (median) and $0.026 per answer (the v1.0 run).
- **Enterprise identity:** Keycloak SSO, company isolation enforced by PostgreSQL Row-Level
  Security, departments and nested groups, a hash-chained audit log, scoped access tokens.
- **Knowledge with an owner and a review date.** Every document has an owner who is reminded
  when its review is due; "official" (what answers prefer) needs a second person's approval;
  answers mark an official source whose review is overdue; the audit log can be filtered and
  exported as CSV ([docs/governance.md](docs/governance.md)).

## What makes it different

- **Permissions are enforced by code, never by the LLM.** Every agent and tool acts as the
  requesting user. A red-team suite with canary tokens checks for **zero leaks**: CI's
  permission gate fails on any leak (it becomes a merge requirement once the repository is
  public and branch protection requires the check, which is not set up yet).
- **Grounded or silent.** Every claim cites a source you can open, or the assistant says it
  doesn't know.
- **Measured, not claimed.** Each release publishes retrieval quality, faithfulness, latency
  and cost numbers.
- **Open source and model-agnostic.** Runs fully local (Ollama/vLLM) or with paid providers
  through swappable adapters. Sensitive data can be pinned to local models.

## Editions

Community (everything outside `ee/`) is complete and Apache-2.0, security basics included.
Enterprise adds features for large organizations from `ee/` and runs only with a license key.
Built so far: department budgets, the chargeback report, retention policies and the export of
the audit chain for a SIEM; with them the boundary, license checks and development licenses
(`make ee-dev-license`). See [docs/enterprise.md](docs/enterprise.md).

## Local development

Requirements: [uv](https://docs.astral.sh/uv/), Node.js 22+, [pnpm](https://pnpm.io) 10.

```bash
make install       # backend + frontend dependencies
make infra         # Postgres, Valkey, Qdrant, OpenFGA, Keycloak, Caddy in Docker
make api-dev       # API on your machine with auto-reload
make web-dev       # UI on your machine with hot reload
make lint test     # what CI runs
```

Open **http://localhost:8480** as usual. In this mode Caddy forwards `/api` and the UI to the
processes on your machine, so login, cookies and redirects behave exactly as in Docker.
Stop the full stack (`make down`) before `make infra` so ports don't clash. Keycloak's
back-channel logout cannot reach a host process, so remote logout is only tested with `make up`.

## Running it in production

Not yet: v1.0 is not released. What exists for it so far, and what has been checked:

- **Kubernetes.** A Helm chart in [`deploy/helm/workmind`](deploy/helm/workmind), with optional
  bundled Postgres, Valkey, Qdrant, OpenFGA, Keycloak and embedding server. It passes
  `helm lint`, renders for Kubernetes 1.33 and validates with kubeconform, and every pod meets
  the chart's security rules (`make helm-check`). It was installed, tested, upgraded and
  uninstalled on local kind clusters (Kubernetes 1.34 and 1.37, `make helm-kind-test`); it has
  **not run on a cloud cluster**, nor with TLS or managed databases
  ([docs/deploy/kubernetes.md](docs/deploy/kubernetes.md#tested-on-a-cluster)).
- **Backups.** `make backup` and `make restore` for the Compose stack: databases, Qdrant
  snapshots, files and the license, with checksums. A restore drill into a separate stack gave
  identical rows, points and files and took about a minute at the demo's size
  ([backup and restore](docs/operations/backup-restore.md)).
- **Upgrades.** Tested from v0.1.0 to this code: 17 of 17 checks after the upgrade, and a
  rollback by restoring the pre-upgrade backup ([upgrades](docs/operations/upgrade.md)).
- **Several companies on one deployment.** Isolation per layer, `wm tenant create|delete`, rate
  limits, and a test that sends every route another tenant's ids
  ([docs/multi-tenancy.md](docs/multi-tenancy.md)).
- **Security.** A [threat model](docs/security/threat-model.md), a review against the
  [OWASP Top 10 for LLM Applications](docs/security/owasp-llm-top10.md) and a red team of 59
  attacks; dependency, image, chart and workflow scans with SBOMs
  ([supply chain](docs/security/supply-chain.md)); how to report a vulnerability:
  [SECURITY.md](SECURITY.md).
- **Releases.** release-please and a release workflow for multi-arch images with SBOM and
  provenance attestations ([releasing](docs/operations/releasing.md), [CHANGELOG](CHANGELOG.md)).
  These workflows have not run yet: there is no public repository.
- **Load tests.** A Locust harness with a stub model server ([docs/load-testing.md](docs/load-testing.md)),
  run from 10 to 800 simulated users on one laptop, and tuned: up to 400 users at unloaded chat
  latency; a sizing table that is an extrapolation ([load benchmarks](docs/benchmarks/load.md)).
- **Docs site.** `make docs-serve` serves every page on http://127.0.0.1:8495, with the
  configuration and HTTP API reference generated from the code.

## Architecture

```text
Browser / MCP clients ──► Caddy ──┬─► Next.js web          (UI)
                                  ├─► FastAPI api          (REST + SSE, auth BFF, MCP)
                                  └─► Keycloak             (login, SSO)
FastAPI ──► PostgreSQL · Qdrant · Valkey · OpenFGA · LLM gateway (local or paid models)
Celery workers (from Phase 2) ──► ingestion, connectors, long agent runs, workflows
```

- [Architecture](docs/ARCHITECTURE.md): every component, flow and decision, end to end.
- [Roadmap](docs/ROADMAP.md): 13 phases from foundation to v1.0, each a working release.
- [Decision records](docs/adr/): why things are the way they are.

## Repository layout

```text
apps/api      FastAPI backend (Python 3.13, uv)
apps/web      Next.js frontend (TypeScript, pnpm)
deploy/       Postgres init, Caddy, the Keycloak realm, the Helm chart (deploy/helm)
seed/         Acme Robotics: the fictional demo company and test corpus
evals/        Evaluation datasets, runners and published results
ee/           Enterprise Edition (separate license): budgets, chargeback, retention, SIEM export
docs/         The docs site (mkdocs.yml): guides, architecture, roadmap, ADRs, security, operations
e2e/browser   Browser end-to-end tests (Playwright) against a running stack; the demo recorder
loadtests/    Load tests (Locust) and the stub model server
scripts/      Backup, restore, upgrade test, supply-chain scans
```

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md). Commits need a DCO sign-off
(`git commit -s`). Report security issues privately: see [SECURITY.md](SECURITY.md).

## License

[Apache License 2.0](LICENSE), except the [`ee/`](ee/) directory, which is under a separate
commercial license. All demo data in this repository is fictional.
