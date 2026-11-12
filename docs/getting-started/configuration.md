# Configuration

WorkMind is configured in two places:

- **The deployment**, by whoever runs it: environment variables, usually in a `.env` file at
  the repository root. They choose the services, the default models, limits and keys. Every
  setting the API reads is in the [configuration reference](../reference/configuration.md),
  which is generated from the code.
- **Each organization**, by its owners and admins, in the app: model providers and keys
  ([Models and keys](../models.md)), agents ([Agents](../agents.md)), budgets
  ([Usage and budgets](../usage.md)), collections and their access, sources
  ([Connectors](../connectors.md)), workflows ([Automation](../automation.md)) and retention
  ([Enterprise edition](../enterprise.md)). These are stored in the database and take effect
  without a restart.

An organization that configures nothing gets what the deployment is configured with.

## Where settings are read

| Process | Reads |
|---|---|
| `api`, `worker`, `beat`, `seed` (Docker Compose) | `.env` through `env_file`, then the values `compose.yaml` sets for them. For the service addresses (`DATABASE_URL`, `VALKEY_URL`, `QDRANT_URL`, `OPENFGA_API_URL`, `KEYCLOAK_URL`, `OBJECT_STORAGE_PATH`, ...), `compose.yaml` wins: they point at the containers. |
| The API on the host (`make api-dev`) | Environment variables, then `.env` files: `apps/api/.env` if there is one, then the one at the repository root. The defaults point at the infrastructure that `make infra` starts. |
| `docker compose` itself | `.env` for the variables in the next section: ports, database passwords, profiles. |

Change `.env`, then `make up` to apply it. Setting names are not case-sensitive.

## Ports, passwords and Compose

These are read by `compose.yaml`, not by the API. `.env.example` lists them with their
development defaults.

| Variable | Default | What it sets |
|---|---|---|
| `HTTP_PORT` | `8480` | The main entry point (Caddy): UI, API and login |
| `PUBLIC_URL` | `http://localhost:8480` | The URL people open. Keep it in step with `HTTP_PORT`; it is passed to the API and to Keycloak |
| `API_PORT`, `WEB_PORT` | `8400`, `3400` | Direct access to the API and the UI, for debugging |
| `POSTGRES_PORT`, `VALKEY_PORT`, `QDRANT_PORT`, `OPENFGA_PORT`, `MAILPIT_PORT` | `5432`, `6379`, `6333`, `8401`, `8025` | Infrastructure ports on `127.0.0.1` |
| `POSTGRES_PASSWORD`, `WORKMIND_OWNER_PASSWORD`, `WORKMIND_APP_PASSWORD`, `KEYCLOAK_DB_PASSWORD`, `OPENFGA_DB_PASSWORD` | development values | The database roles' passwords, set when the database is first created |
| `KEYCLOAK_ADMIN_USER`, `KEYCLOAK_ADMIN_PASSWORD` | development values | Keycloak's bootstrap admin, also used by `make seed` |
| `OIDC_CLIENT_SECRET` | development value | Shared by Keycloak's `workmind-api` client and the API |
| `OPENFGA_API_KEY` | development value | Shared by OpenFGA and the API |
| `COMPOSE_PROFILES` | unset | `embeddings` runs the embedding model in a container (`make quickstart` sets it) |
| `NEXT_PUBLIC_DEMO` | `1` | Shows the **View as…** demo switcher in the UI; set `0` outside demos |

Every host port binds to `127.0.0.1`, so nothing is reachable from your network by default.

## Before anything is shared

The defaults are for a laptop. Before a deployment that other people can reach:

- [ ] `APP_ENV=production` (or `demo` for a public demo). In production, the API refuses to start
      with the development `OIDC_CLIENT_SECRET`, `OPENFGA_API_KEY` or `SECRETS_KEY`, and does not
      serve the interactive API docs.
- [ ] `SECRETS_ENCRYPTION_KEY` (organizations' model keys) and `SECRETS_KEY` (connector
      credentials): 32 random bytes each, base64. `SECRETS_ENCRYPTION_KEY` is required for every
      `APP_ENV` except `dev` and `test`. Generate one with:

    ```bash
    python -c "import base64,os; print(base64.b64encode(os.urandom(32)).decode())"
    ```

- [ ] New values for every password and shared secret in the previous section.
- [ ] `PUBLIC_URL` with `https://`: session cookies are then `Secure` and use the `__Host-`
      prefix.
- [ ] `NEXT_PUBLIC_DEMO=0`, and no `make seed`: the seed tool creates demo logins with a known
      password.
- [ ] Decide which documents may reach an external model: the data policy in
      [Models and keys](../models.md#the-data-policy-what-may-leave). New organizations default
      to keeping `restricted` documents away from external models.

This list covers the settings. Running WorkMind in production also means TLS, backups and
upgrades, which this page does not cover.

## Common changes

| To | Set | Read |
|---|---|---|
| Use Claude | `ANTHROPIC_API_KEY` | [Models and keys](../models.md) |
| Use a local or self-hosted model | `LLM_PROVIDER=openai_compatible`, `LLM_BASE_URL`, `LLM_MODEL` | [Local models](local-models.md) |
| Use another model for one kind of work | `LLM_MODEL_ANSWER`, `_ROUTER`, `_AGENT`, `_GENERATOR`, `_JUDGE` | [Models and keys](../models.md) |
| Keep restricted documents on a local model | `LOCAL_LLM_BASE_URL`, `LOCAL_LLM_MODEL` | [Local models](local-models.md#keep-restricted-documents-local) |
| Route questions to department agents | `ASSISTANT_MODE=auto` or `agents` | [Agents](../agents.md) |
| Send email (after approval) | `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_STARTTLS` | [Automation](../automation.md#email) |
| Sync Google Drive | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | [Connectors](../connectors.md) |
| Give the research agent web search | `SEARXNG_URL` | [Configuration reference](../reference/configuration.md#searxng_url) |
| Give agents tools from other MCP servers | `MCP_SERVERS` | [MCP](../mcp.md#the-other-direction-workmind-using-mcp-tools) |
| Transcribe meetings elsewhere, or not at all | `TRANSCRIPTION_PROVIDER`, `TRANSCRIPTION_MODEL`, `TRANSCRIPTION_BASE_URL` | [Meetings](../meetings.md) |
| Limit analytics queries | `ANALYTICS_MAX_ROWS`, `ANALYTICS_TIMEOUT_S`, `ANALYTICS_WORKERS` | [Insights](../analytics.md) |
| Turn on Enterprise features | `LICENSE_KEY` or `LICENSE_FILE` (development: `make ee-dev-license`) | [Enterprise edition](../enterprise.md) |
| Send traces to an OTLP backend | `OTEL_EXPORTER_OTLP_ENDPOINT`, optionally `OTEL_SERVICE_NAME` | below |

**Tracing.** The API reads the standard OpenTelemetry variables directly (they are not in the
reference): with `OTEL_EXPORTER_OTLP_ENDPOINT` set, it exports traces to any OTLP-compatible
backend; without it, tracing is off.

## The `wm` command

The API image and the development environment (`cd apps/api && uv run wm ...`) include a
command line for operators. With Docker Compose, run it in a container that has the settings,
for example `docker compose exec api wm secrets rotate`.

| Command | What it does |
|---|---|
| `wm reindex <tenant>` | Re-index every document of an organization (after changing the embedding model) |
| `wm secrets rotate` | Re-encrypt stored model provider keys with the current `SECRETS_ENCRYPTION_KEY` ([rotation](../models.md#rotating-the-key)) |
| `wm usage backfill [<tenant>]` | Import usage recorded before usage events existed; safe to run again ([Usage](../usage.md)) |
| `wm license dev` | Write a development Enterprise license (`APP_ENV=dev` only; `make ee-dev-license`) |
| `wm eval` | Run the evaluation suites against a running, seeded stack ([Evaluation](../evals/README.md)) |
| `wm seed` | Create a demo company from a company file (development only; `make seed`) |
