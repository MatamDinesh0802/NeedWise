# WorkMind AI

**The open-source enterprise AI assistant that is permission-safe, cited, measured and self-hosted.**

Employees ask questions in plain language and get answers grounded in company knowledge, with
citations, drawn only from sources they are allowed to see. Specialized agents (HR, Finance,
Engineering…) handle department work. Actions that leave the system need human approval.

> **Status: pre-alpha, built in public.** Phases 0–3 are complete and the chat assistant
> (Phase 4) is landing. Nothing here is ready for real company data yet. Follow along in the
> [roadmap](docs/ROADMAP.md).

## What works today

- **Ask questions, get cited answers.** Streaming chat over company documents: every claim
  cites a source you can open, follow-up questions are understood, and the assistant says
  so when it doesn't know.
- **Permission-safe by construction.** Search filters every chunk by its access list (lock 1),
  then re-checks each source with OpenFGA (lock 2). The answer model only ever sees what the
  asking person may read. A red-team suite with canary tokens must show **zero leaks** in CI.
- **Measured.** `wm eval` asks 104 questions as 11 different employees: 0 leaks, 81/82
  correct, 99.8% faithfulness, $0.017 per answer ([evaluation](evals/README.md)).
- **Restricted data stays local** if you want: per-company policy routes restricted sources
  to a local model, or leaves them out and says so.
- **Knowledge Hub:** upload PDF, Word, Excel, PowerPoint, Markdown, HTML, CSV or Slack exports;
  watch them get indexed; see exactly which chunks the assistant reads.
- **Enterprise identity:** Keycloak SSO, company isolation enforced by PostgreSQL Row-Level
  Security, departments and nested groups, a hash-chained audit log, scoped access tokens.

## What makes it different

- **Permissions are enforced by code, never by the LLM.** Every agent and tool acts as the
  requesting user. A public red-team suite with canary tokens must show **zero leaks** for a
  change to merge.
- **Grounded or silent.** Every claim cites a source you can open, or the assistant says it
  doesn't know.
- **Measured, not claimed.** Each release publishes retrieval quality, faithfulness, latency
  and cost numbers.
- **Open source and model-agnostic.** Runs fully local (Ollama/vLLM) or with paid providers
  through swappable adapters. Sensitive data can be pinned to local models.

## Quick start

Requirements: Docker with Compose v2.

```bash
git clone <repo-url> workmind && cd workmind
make quickstart    # the stack, a local embedding model in Docker, and the Acme demo company
```

Open **http://localhost:8480** and sign in as `alice@acme.test` / `workmind-demo`. The first
run downloads about 2.5 GB (images and the embedding model) and takes a few minutes.

**For answers, add a model** to `.env` (git-ignored), then `make up`:

| Option | `.env` |
|---|---|
| Claude (default) | `ANTHROPIC_API_KEY=sk-ant-...` |
| Fully local (Ollama, vLLM, llama.cpp) | `LLM_PROVIDER=openai_compatible`, `LLM_BASE_URL=http://host.docker.internal:11434/v1`, `LLM_MODEL=qwen3:14b` |

Every model role (answers, question rewriting, judging) can be set separately, and restricted
documents can be pinned to a local model ([ADR 0007](docs/adr/0007-model-providers-and-lightweight-parsing.md),
[ADR 0008](docs/adr/0008-retrieval-fusion-and-sensitivity-routing.md)).

> **On a Mac?** `make embeddings` runs the embedding model on the Apple GPU instead
> (`brew install llama.cpp`; set `EMBEDDING_BASE_URL=http://host.docker.internal:8091/v1`).
> Both produce the same vectors, so you can switch without re-indexing.

Demo logins use the password `workmind-demo` (development only): `alice@acme.test` (HR manager),
`bob@acme.test` (engineer), `carol@acme.test` (finance), `dan@acme.test` (sales),
`erin@acme.test` (IT admin), `mallory@acme.test` (external contractor). Ask each of them
"What is the salary band for an L3 engineer?" and compare. See [seed/README.md](seed/README.md).

| URL | What |
|---|---|
| http://localhost:8480 | The app: UI, API and login on one origin, via Caddy |
| http://localhost:8480/api/docs | Interactive API docs |
| http://localhost:8480/auth | Keycloak (admin console login: `admin` / `admin`, dev only) |

`make status` prints component health; `make` lists every command.

> **macOS memory tip:** the core stack needs about 3 GB. Docker Desktop's memory limit can stay
> around 8 GB. Run local LLMs in Ollama **natively** (Docker cannot use the Apple GPU), and
> keep the rest of your RAM for them.

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
deploy/       Postgres init, Caddy, later Keycloak realm and OpenFGA model
seed/         Acme Robotics: the fictional demo company and test corpus
evals/        Evaluation datasets, runners and published results
ee/           Enterprise Edition (separate license; currently empty)
docs/         Architecture, roadmap, ADRs
```

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md). Commits need a DCO sign-off
(`git commit -s`). Report security issues privately: see [SECURITY.md](SECURITY.md).

## License

[Apache License 2.0](LICENSE), except the [`ee/`](ee/) directory, which is under a separate
commercial license. All demo data in this repository is fictional.
