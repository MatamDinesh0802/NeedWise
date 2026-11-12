# Configuration reference

<!-- Generated from apps/api/src/workmind/core/config.py by `make docs-gen`. Do not edit:
     tests/test_docs_reference.py fails when this page is out of date. -->

The API, the workers, beat and the seed tool read these settings from environment variables, or
from a `.env` file at the repository root (git-ignored; `.env.example` lists the common ones).
Names are not case-sensitive. With Docker Compose, `compose.yaml` passes `.env` to the
containers and sets the service addresses itself (`DATABASE_URL`, `VALKEY_URL`, ...): for
those, its values win. What to set, and what production requires:
[Configuration](../getting-started/configuration.md).

Types marked *secret* hold credentials: keep them out of files you commit. A secret's default is
not shown here; where one exists it is a development value.

## Deployment

| Variable | Type | Default | Description |
|---|---|---|---|
| <a id="product_name"></a>`PRODUCT_NAME` | string | `WorkMind AI` | The product name in the API's title and in logs. |
| <a id="app_env"></a>`APP_ENV` | one of `dev`, `test`, `demo`, `production` | `dev` | Where this deployment runs. `production` refuses the development values of `OIDC_CLIENT_SECRET`, `OPENFGA_API_KEY` and `SECRETS_KEY`, and turns off the interactive API docs; every value except `dev` and `test` requires `SECRETS_ENCRYPTION_KEY`; development licenses are trusted only in `dev`. |
| <a id="log_level"></a>`LOG_LEVEL` | string | `INFO` | The lowest level that is logged (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |
| <a id="log_format"></a>`LOG_FORMAT` | one of `console`, `json` | `console` | `console` for people, `json` for log collectors. Both carry the same fields (timestamp, level, logger, request id). |
| <a id="public_url"></a>`PUBLIC_URL` | string | `http://localhost:8480` | The URL people type into the browser. Login redirects and cookie security derive from it: with `https://`, the session cookie is `Secure` and named `__Host-wm_session`. |
| <a id="health_check_timeout_s"></a>`HEALTH_CHECK_TIMEOUT_S` | number, > 0 | `2.0` | Time limit of each component check in `/readyz` and `/api/v1/system/status`, in seconds. |

## Services

| Variable | Type | Default | Description |
|---|---|---|---|
| <a id="database_url"></a>`DATABASE_URL` | string | `postgresql+asyncpg://workmind_app:workmind_app@localhost:5432/workmind` | PostgreSQL as the runtime role: no `BYPASSRLS`, data rights only, so row-level security applies to every query (ADR 0005). |
| <a id="migrations_database_url"></a>`MIGRATIONS_DATABASE_URL` | string | `postgresql+asyncpg://workmind_owner:workmind_owner@localhost:5432/workmind` | PostgreSQL as the owner role, used only by Alembic migrations (the `migrate` service). |
| <a id="db_app_role"></a>`DB_APP_ROLE` | string | `workmind_app` | Name of the runtime role; migrations grant and revoke its privileges. |
| <a id="valkey_url"></a>`VALKEY_URL` | string | `redis://localhost:6379/0` | Valkey (or Redis): sessions, caches and the event streams of answers. |
| <a id="qdrant_url"></a>`QDRANT_URL` | string | `http://localhost:6333` | Qdrant, which holds the search index of document chunks. |
| <a id="openfga_api_url"></a>`OPENFGA_API_URL` | string | `http://localhost:8401` | OpenFGA, which answers permission checks (the second lock). |
| <a id="openfga_api_key"></a>`OPENFGA_API_KEY` | *secret* | *development value* | Pre-shared key for OpenFGA. Production refuses the development value. |
| <a id="authz_outbox_worker"></a>`AUTHZ_OUTBOX_WORKER` | boolean | `true` | Copy permission changes from PostgreSQL to OpenFGA in the background (the outbox). Off only in unit tests. |
| <a id="celery_broker_url"></a>`CELERY_BROKER_URL` | string | `redis://localhost:6379/1` | The Celery broker (a Valkey database) for background jobs. |

## Sign-in and sessions

| Variable | Type | Default | Description |
|---|---|---|---|
| <a id="keycloak_url"></a>`KEYCLOAK_URL` | string | `http://localhost:8480/auth` | Keycloak's base URL as the API reaches it, for OpenID Connect discovery and tokens. |
| <a id="keycloak_realm"></a>`KEYCLOAK_REALM` | string | `workmind` | The Keycloak realm that holds the logins. |
| <a id="oidc_client_id"></a>`OIDC_CLIENT_ID` | string | `workmind-api` | The OpenID Connect client the API signs people in with (ADR 0003). |
| <a id="oidc_client_secret"></a>`OIDC_CLIENT_SECRET` | *secret* | *development value* | That client's secret, shared with Keycloak. Production refuses the development value. |
| <a id="session_idle_timeout_s"></a>`SESSION_IDLE_TIMEOUT_S` | integer, > 0 | `1800` | A session ends after this many seconds without a request. |
| <a id="session_absolute_timeout_s"></a>`SESSION_ABSOLUTE_TIMEOUT_S` | integer, > 0 | `43200` | A session ends this many seconds after sign-in, however active it is. |

## Encryption keys

| Variable | Type | Default | Description |
|---|---|---|---|
| <a id="secrets_key"></a>`SECRETS_KEY` | *secret* | *development value* | Encrypts connector credentials at rest (AES-256-GCM): 32 random bytes, base64. Production refuses the development value. |
| <a id="secrets_encryption_key"></a>`SECRETS_ENCRYPTION_KEY` | *secret* | *unset* | Encrypts organizations' model provider keys at rest (AES-256-GCM): 32 random bytes, base64. Required unless `APP_ENV` is `dev` or `test`. [Rotating it](../models.md#rotating-the-key). |
| <a id="secrets_encryption_previous_keys"></a>`SECRETS_ENCRYPTION_PREVIOUS_KEYS` | *secret* | *unset* | Older keys, comma-separated, that only decrypt: set during a rotation, then run `wm secrets rotate`. |

## Language models

| Variable | Type | Default | Description |
|---|---|---|---|
| <a id="llm_provider"></a>`LLM_PROVIDER` | one of `anthropic`, `openai_compatible` | `anthropic` | The deployment's model provider: `anthropic`, or `openai_compatible` for any `/v1/chat/completions` server (Ollama, vLLM, llama.cpp, LM Studio, gateways). |
| <a id="anthropic_api_key"></a>`ANTHROPIC_API_KEY` | *secret* | *unset* | API key for the `anthropic` provider. Without a key (here or an organization's own in Admin > Models), search works but answers do not. |
| <a id="llm_base_url"></a>`LLM_BASE_URL` | string | `http://localhost:11434/v1` | The `openai_compatible` server. |
| <a id="llm_api_key"></a>`LLM_API_KEY` | *secret* | *unset* | API key for `LLM_BASE_URL`, if the server needs one. |
| <a id="llm_model"></a>`LLM_MODEL` | string | `claude-sonnet-5-5` | The default model for every role. |
| <a id="llm_model_answer"></a>`LLM_MODEL_ANSWER` | string | *unset* | Model for the `answer` role (cited answers). Unset: `LLM_MODEL`. |
| <a id="llm_model_router"></a>`LLM_MODEL_ROUTER` | string | *unset* | Model for the `router` role (rewriting questions, planning). Unset: `LLM_MODEL`. |
| <a id="llm_model_judge"></a>`LLM_MODEL_JUDGE` | string | *unset* | Model for the `judge` role (evaluations). Unset: `LLM_MODEL`. |
| <a id="llm_model_generator"></a>`LLM_MODEL_GENERATOR` | string | *unset* | Model for the `generator` role (meeting notes). Unset: `LLM_MODEL`. |
| <a id="llm_model_agent"></a>`LLM_MODEL_AGENT` | string | *unset* | Model for the `agent` role (department, research and analytics agents). Unset: `LLM_MODEL`. |
| <a id="assistant_mode"></a>`ASSISTANT_MODE` | one of `single`, `agents`, `auto` | `single` | `single`: one search and one answer. `agents`: a planner routes every question to department agents. `auto`: agents only for questions that span several areas. `single` was chosen by measurement (ADR 0009). |
| <a id="agent_fast_path"></a>`AGENT_FAST_PATH` | boolean | `true` | Skip the planner when one agent's profile clearly matches the question. |
| <a id="agent_fast_path_min_score"></a>`AGENT_FAST_PATH_MIN_SCORE` | number | `0.3` | Fast path: the lowest similarity between a question and the best agent's profile (calibrated for EmbeddingGemma; see the evaluation). |
| <a id="agent_fast_path_min_margin"></a>`AGENT_FAST_PATH_MIN_MARGIN` | number | `0.15` | Fast path: how much closer the best agent must be than the second. |
| <a id="llm_fallbacks"></a>`LLM_FALLBACKS` | boolean | `true` | Anthropic models that support it: a request the model declines is re-run on a model Anthropic picks (server-side `default` fallback). |
| <a id="llm_external"></a>`LLM_EXTERNAL` | boolean | `false` | Set `true` when `LLM_BASE_URL` is a third-party API rather than a model you run: the data policy then treats it as external. |
| <a id="local_llm_base_url"></a>`LOCAL_LLM_BASE_URL` | string | *unset* | An optional local model (OpenAI-compatible) for organizations whose policy keeps restricted data off external APIs. Without one, those sources are left out of answers from external models, and the answer says so. |
| <a id="local_llm_model"></a>`LOCAL_LLM_MODEL` | string | *unset* | The local model's name. |
| <a id="local_llm_api_key"></a>`LOCAL_LLM_API_KEY` | *secret* | *unset* | The local model server's API key, if it needs one. |

## Embeddings and search

| Variable | Type | Default | Description |
|---|---|---|---|
| <a id="embedding_provider"></a>`EMBEDDING_PROVIDER` | one of `openai_compatible`, `hash` | `openai_compatible` | `openai_compatible`: any `/v1/embeddings` server. `hash`: deterministic vectors without a model, for tests and CI (search quality is meaningless). |
| <a id="embedding_base_url"></a>`EMBEDDING_BASE_URL` | string | `http://localhost:8091/v1` | The embedding server: `make embeddings` serves port 8091 on the host; the `embeddings` compose profile serves `http://embeddings:8080/v1`. |
| <a id="embedding_api_key"></a>`EMBEDDING_API_KEY` | *secret* | *unset* | The embedding server's API key, if it needs one. |
| <a id="embedding_model"></a>`EMBEDDING_MODEL` | string | `embeddinggemma-300m` | The embedding model. The search index is named after it and its dimensions, so changing either starts a new index: re-index with `wm reindex <tenant>`. |
| <a id="embedding_dimensions"></a>`EMBEDDING_DIMENSIONS` | integer, > 0 | `768` | Vector size of the embedding model (768 for EmbeddingGemma-300M). |
| <a id="embedding_query_template"></a>`EMBEDDING_QUERY_TEMPLATE` | string | <code>task: search result &#124; query: {text}</code> | How a search query is presented to the embedding model. |
| <a id="embedding_document_template"></a>`EMBEDDING_DOCUMENT_TEMPLATE` | string | <code>title: {title} &#124; text: {text}</code> | How a chunk is presented to the embedding model. |
| <a id="retrieval_sparse_weight"></a>`RETRIEVAL_SPARSE_WEIGHT` | number, ≥ 0 | `0.5` | Hybrid search: BM25's weight in rank fusion, relative to dense search (1.0). Chosen by measurement (ADR 0008). |
| <a id="retrieval_rrf_k"></a>`RETRIEVAL_RRF_K` | integer, ≥ 1 | `2` | Hybrid search: the k of reciprocal rank fusion (ADR 0008). |
| <a id="retrieval_min_similarity"></a>`RETRIEVAL_MIN_SIMILARITY` | number, ≥ 0, ≤ 1 | `0.25` | Relevance floor: when the best dense similarity is below it, nothing is sent to a model and the assistant says it found nothing. Calibrated for EmbeddingGemma; recalibrate for another embedding model. |

## Documents and ingestion

| Variable | Type | Default | Description |
|---|---|---|---|
| <a id="object_storage_path"></a>`OBJECT_STORAGE_PATH` | string | `<repo>/.data/objects` | Directory for original files, parsed documents, recordings and analytics datasets. Compose mounts `<repo>/.data/objects` there as `/data/objects`. |
| <a id="ingestion_mode"></a>`INGESTION_MODE` | one of `celery`, `inline` | `celery` | `celery`: workers index uploads and syncs. `inline`: the API process does it. |
| <a id="max_upload_bytes"></a>`MAX_UPLOAD_BYTES` | integer, > 0 | `52428800` (50 MiB) | The largest document upload. |

## Meetings

| Variable | Type | Default | Description |
|---|---|---|---|
| <a id="max_meeting_bytes"></a>`MAX_MEETING_BYTES` | integer, > 0 | `524288000` (500 MiB) | The largest meeting recording or transcript. |
| <a id="transcription_provider"></a>`TRANSCRIPTION_PROVIDER` | one of `faster_whisper`, `openai_compatible`, `none` | `faster_whisper` | Speech to text for meetings: `faster_whisper` on the worker's CPU, `openai_compatible` for any `/v1/audio/transcriptions` server, or `none` (transcripts only). |
| <a id="transcription_model"></a>`TRANSCRIPTION_MODEL` | string | `base.en` | A faster-whisper model (`base.en`, `small.en`, `medium.en`: slower and more accurate), or the server's model. |
| <a id="transcription_base_url"></a>`TRANSCRIPTION_BASE_URL` | string | `http://localhost:8000/v1` | The transcription server (`openai_compatible` only). |
| <a id="transcription_api_key"></a>`TRANSCRIPTION_API_KEY` | *secret* | *unset* | The transcription server's API key, if it needs one. |

## Analytics

| Variable | Type | Default | Description |
|---|---|---|---|
| <a id="analytics_max_rows"></a>`ANALYTICS_MAX_ROWS` | integer, > 0, ≤ 10000 | `1000` | Row cap per analytics query. |
| <a id="analytics_timeout_s"></a>`ANALYTICS_TIMEOUT_S` | number, > 0, ≤ 120 | `10.0` | Time limit per analytics query, in seconds. |
| <a id="analytics_workers"></a>`ANALYTICS_WORKERS` | integer, ≥ 1, ≤ 32 | `3` | Analytics query worker processes: how many queries run at once. |
| <a id="analytics_worker_memory_mb"></a>`ANALYTICS_WORKER_MEMORY_MB` | integer, ≥ 256, ≤ 65536 | `1024` | A query worker is stopped above this resident memory. |
| <a id="analytics_queries_per_user"></a>`ANALYTICS_QUERIES_PER_USER` | integer, ≥ 1, ≤ 32 | `1` | How many of one person's queries run at once; more wait. |

## Tools and connectors

| Variable | Type | Default | Description |
|---|---|---|---|
| <a id="searxng_url"></a>`SEARXNG_URL` | string | *unset* | A self-hosted SearXNG for the research agent's `web_search` tool. Unset: no web search. |
| <a id="mcp_servers"></a>`MCP_SERVERS` | JSON list | `[]` | Remote MCP servers whose tools agents may use, as JSON: `[{"key": "erp", "url": "https://erp.example/mcp", "token": "...", "read_only": ["stock"]}]`. A tool not listed in `read_only` needs approval. |
| <a id="google_client_id"></a>`GOOGLE_CLIENT_ID` | string | *unset* | Google Drive sources: an OAuth client of type "Web application", with the redirect URI `<PUBLIC_URL>/api/v1/oauth/google/callback`. |
| <a id="google_client_secret"></a>`GOOGLE_CLIENT_SECRET` | *secret* | *unset* | That OAuth client's secret. |

## Email

| Variable | Type | Default | Description |
|---|---|---|---|
| <a id="smtp_host"></a>`SMTP_HOST` | string | *unset* | Outgoing email (`send_email`, always after a person's approval). Unset: email is off. Development uses Mailpit, which delivers nothing: http://localhost:8025. |
| <a id="smtp_port"></a>`SMTP_PORT` | integer | `587` | The SMTP port. |
| <a id="smtp_from"></a>`SMTP_FROM` | string | `WorkMind <workmind@localhost>` | The sender of outgoing email. |
| <a id="smtp_username"></a>`SMTP_USERNAME` | string | *unset* | SMTP login, if the server needs one. |
| <a id="smtp_password"></a>`SMTP_PASSWORD` | *secret* | *unset* | SMTP password, if the server needs one. |
| <a id="smtp_starttls"></a>`SMTP_STARTTLS` | boolean | `false` | Upgrade the SMTP connection with STARTTLS. |

## Enterprise edition

| Variable | Type | Default | Description |
|---|---|---|---|
| <a id="license_key"></a>`LICENSE_KEY` | *secret* | *unset* | The Enterprise license token. Unset: `LICENSE_FILE`, else `license.key` in `/run/workmind` or `<repo>/.workmind`. Without a valid license: Community. |
| <a id="license_file"></a>`LICENSE_FILE` | path | *unset* | A file that holds the license token. |
| <a id="license_dev_public_key"></a>`LICENSE_DEV_PUBLIC_KEY` | string | *unset* | Public key of the development license signer (raw Ed25519, base64url), trusted only when `APP_ENV=dev`. Unset: `dev-public.key` where the license is looked for. |
| <a id="license_dev_public_key_file"></a>`LICENSE_DEV_PUBLIC_KEY_FILE` | path | *unset* | A file that holds that key. |
| <a id="deployment_id"></a>`DEPLOYMENT_ID` | string | *unset* | This deployment's id: a license issued for one deployment is valid only where it matches. |

## Demo data (development only)

| Variable | Type | Default | Description |
|---|---|---|---|
| <a id="keycloak_admin_user"></a>`KEYCLOAK_ADMIN_USER` | string | `admin` | The Keycloak admin login the seed tool (`make seed`) creates demo logins with. |
| <a id="keycloak_admin_password"></a>`KEYCLOAK_ADMIN_PASSWORD` | *secret* | *development value* | That login's password. |
| <a id="demo_password"></a>`DEMO_PASSWORD` | *secret* | *development value* | The password of the demo logins that `make seed` creates. |
