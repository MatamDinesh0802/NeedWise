# WorkMind AI

**The open-source enterprise AI assistant that is permission-safe, cited, measured and self-hosted.**

Employees ask questions in plain language and get answers grounded in company knowledge, with
citations, drawn only from sources they are allowed to see. Specialized agents (HR, Finance,
Engineering…) handle department work. Actions that leave the system need human approval.

> **Status: pre-alpha, built in public.** Phase 0 (foundation) is complete. Nothing here is
> ready for real company data yet. Follow along in the [roadmap](docs/ROADMAP.md).

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

Requirements: Docker with Compose v2. That's it for running the stack.

```bash
git clone <repo-url> workmind && cd workmind
make up            # or: docker compose up -d --build --wait
```

Open **http://localhost:8480**. The first start pulls about 2 GB of images and takes a few minutes.

| URL | What |
|---|---|
| http://localhost:8480 | The app: UI, API and login on one origin, via Caddy |
| http://localhost:8480/api/docs | Interactive API docs |
| http://localhost:8480/auth | Keycloak (admin console login: `admin` / `admin`, dev only) |

`make status` prints component health; `make` lists every command.

> **macOS memory tip:** the core stack needs about 3 GB. Docker Desktop's memory limit can stay
> around 8 GB. From Phase 2, local models run in Ollama **natively** (not in Docker, which
> cannot use the Apple GPU), so keep the rest of your RAM for them.

## Local development

Requirements: [uv](https://docs.astral.sh/uv/), Node.js 22+, [pnpm](https://pnpm.io) 10.

```bash
make install       # backend + frontend dependencies
make infra         # Postgres, Valkey, Qdrant, OpenFGA, Keycloak, Caddy in Docker
make api-dev       # API on http://localhost:8400 with auto-reload
make web-dev       # UI on http://localhost:3400 with hot reload (proxies /api to :8400)
make lint test     # what CI runs
```

Stop the full stack (`make down`) before `make infra` so ports don't clash.

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
