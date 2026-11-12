# Quickstart

This runs WorkMind on your machine with **Acme Robotics**, a fictional 180-person company with
departments, demo logins and 44 documents, some of them restricted.

## Requirements

- Docker with Compose v2. On macOS, Docker Desktop with about 8 GB of memory is enough: the
  core stack uses about 3 GB.
- About 2.5 GB of downloads on the first run (images and the embedding model). It takes a few
  minutes.
- Free local ports 8480 (the app), 8400, 3400, 8401, 5432, 6379, 6333 and 8025. All of them
  bind to `127.0.0.1` only; [Configuration](configuration.md#ports-passwords-and-compose)
  shows how to move them.

## Start it

```bash
git clone <repo-url> workmind && cd workmind
make quickstart
```

`make quickstart`:

1. copies `.env.example` to `.env` if there is no `.env` (it is git-ignored);
2. runs the embedding model (EmbeddingGemma-300M on llama.cpp) in a container, by adding
   `EMBEDDING_BASE_URL=http://embeddings:8080/v1` and `COMPOSE_PROFILES=embeddings` to `.env`;
3. builds and starts the stack and waits until every service is healthy;
4. creates the demo company (`make seed`): Keycloak logins, departments, groups, collections,
   agents, and the documents, indexed through the real ingestion pipeline.

Open **http://localhost:8480** and sign in.

## Demo logins

Every demo login uses the development password `workmind-demo` (the `DEMO_PASSWORD` setting).
These accounts exist only in a seeded development stack.

| Login | Who | Org role | Can read |
|---|---|---|---|
| `alice@acme.test` | Alice Moreau, HR manager | member | HR, employee records, payroll, company-wide documents |
| `bob@acme.test` | Bob Okafor, software engineer | member | Engineering, the support knowledge base, company-wide documents. **Not** salaries |
| `carol@acme.test` | Carol Lindqvist, finance analyst | member | Finance reports, company-wide documents. **Not** payroll |
| `dan@acme.test` | Dan Whitaker, account executive | member | Sales, company-wide documents |
| `erin@acme.test` | Erin Takahashi, IT administrator | admin | Runs the Admin pages. Being an admin does **not** grant restricted collections |
| `priya.raman@acme.test` | Priya Raman, CEO | owner | Leadership strategy, finance reports, company-wide documents |
| `hannah.schmidt@acme.test` | Hannah Schmidt, CFO | member | Finance reports, payroll, leadership strategy, company-wide documents |
| `olivia.grant@acme.test` | Olivia Grant, head of operations | member | Leadership strategy, finance reports, company-wide documents; edits the Operations SOPs |
| `mallory@acme.test` | Mallory Quinn, external contractor | guest | Only the contractor onboarding collection: the red-team user |

The source of truth is `seed/acme/company.yaml`. In the app, **View as…** in the user menu
signs in as another demo account. It is shown when `NEXT_PUBLIC_DEMO=1`, the default in
`compose.yaml`.

## Add a model for answers

Search, the Knowledge Hub and the admin pages work without a model. Answers, agents, meeting
notes and Insights need one. Add one of these to `.env`, then run `make up`:

| Option | Lines in `.env` |
|---|---|
| Claude (the default provider) | `ANTHROPIC_API_KEY=<your Anthropic API key>` |
| A model on your machine (Ollama, vLLM, llama.cpp, LM Studio) | `LLM_PROVIDER=openai_compatible`<br>`LLM_BASE_URL=http://host.docker.internal:11434/v1`<br>`LLM_MODEL=qwen3:14b` |

[Local models](local-models.md) covers the second option in detail. An organization can also
bring its own keys in the app, in **Admin › Models** ([Models and keys](../models.md)).

## Try it

Ask the same question as two people, and compare:

1. Sign in as `alice@acme.test` and ask *"What is the salary band for an L3 engineer?"* The
   answer cites the salary bands in Employee Records.
2. Sign in as `bob@acme.test` and ask the same. Bob gets *"I couldn't find this in the sources
   available to you"*, with no hint that the document exists.

More to try:

- *"How many weeks of parental leave do we get?"*: an older policy and a Slack thread disagree
  with the official 2026 policy. Answers prefer the official source and mention the
  disagreement.
- **Knowledge Hub**: open a collection you can edit (Alice: Company Handbook) and upload a file;
  watch it get indexed and see its chunks.
- **Meetings** (as `olivia.grant@acme.test`): upload
  `seed/acme/meetings/weekly-ops-sync-2026-11-02.txt` to Operations SOPs. Its decisions and
  action items link to the moment they were said.
- **Insights** (as `dan@acme.test`): *"Show revenue by customer."* Dan only ever sees his own
  13 customers, whatever SQL the model writes; Carol sees all of them.
- **Admin** (as `erin@acme.test`): agents, models, budgets, the audit log.

## Where things are

| URL | What |
|---|---|
| http://localhost:8480 | The app: UI, API and login on one origin, through Caddy |
| http://localhost:8480/api/docs | Interactive API docs (not served when `APP_ENV=production`) |
| http://localhost:8480/auth | Keycloak (its admin console uses `KEYCLOAK_ADMIN_USER` / `KEYCLOAK_ADMIN_PASSWORD`, development values in `.env.example`) |
| http://localhost:8025 | Mailpit: every email WorkMind sends in development lands here, none is delivered |

## Everyday commands

| Command | What it does |
|---|---|
| `make up` | Build and start the stack (after changing `.env`) |
| `make status` | Component health as the API reports it |
| `make logs s=api` | Follow one service's logs |
| `make down` | Stop the stack; data is kept |
| `make reset` | Stop the stack and **delete all local data** |
| `make ee-dev-license` | Turn on the Enterprise features with a development license ([Enterprise edition](../enterprise.md)) |
| `make` | List every command |

## Develop on your machine

Requirements: [uv](https://docs.astral.sh/uv/), Node.js 22 or later, pnpm 10.

```bash
make install    # backend and frontend dependencies
make infra      # Postgres, Valkey, Qdrant, OpenFGA, Keycloak and Caddy in Docker
make api-dev    # the API on the host, with auto-reload
make web-dev    # the UI on the host, with hot reload
make lint test  # what CI runs
```

http://localhost:8480 still serves everything: Caddy forwards `/api` and the UI to the processes
on your machine, so sign-in and cookies behave as in Docker. Stop the full stack (`make down`)
before `make infra` so the ports don't clash.

## If something goes wrong

- **A port is taken.** Set another one in `.env` (`HTTP_PORT` and `PUBLIC_URL` together), then
  `make up`.
- **Seeding or uploads fail with `Permission denied: '/data/objects/...'` (Linux).** The
  containers run as uid 10001, and a Linux Docker daemon creates a missing bind-mount directory
  as root. `make up` and `make quickstart` run `make objects-dir`, which creates `.data/objects`
  and gives it to uid 10001; run it yourself before a plain `docker compose up`. Docker Desktop
  ignores ownership, so macOS and Windows never see this.
- **The stack is slow on a Mac.** Run the embedding model on the Apple GPU instead of in Docker:
  `make embeddings` (needs llama.cpp on the host) and
  `EMBEDDING_BASE_URL=http://host.docker.internal:8091/v1` in `.env`. Both produce the same
  vectors, so nothing needs re-indexing.
- **Answers say a model is missing.** See [Add a model for answers](#add-a-model-for-answers).
- **Anything else:** `make status` names the component that is down; `make logs s=<service>`
  shows why.
