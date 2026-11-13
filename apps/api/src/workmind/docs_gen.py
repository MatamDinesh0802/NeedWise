"""Reference pages generated from the code, for the documentation site (docs/reference/).

    uv run python -m workmind.docs_gen            write them        (make docs-gen)
    uv run python -m workmind.docs_gen --check    exit 1 if a committed page is out of date

- configuration.md: every setting of `Settings` (core/config.py) with its environment variable,
  type, default and description. A description is the field's `description`, else its entry in
  NOTES below, else the comment above the field (or at the end of its line) in config.py.
- openapi.json: the OpenAPI schema of the HTTP API as a Community deployment serves it (no
  license, so no Enterprise routes), built from the code with default settings, independent of
  the environment and any .env file.

tests/test_docs_reference.py fails when a committed page differs from what this module writes.
"""

import ast
import fnmatch
import html
import io
import json
import sys
import tokenize
import types
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal, Union, get_args, get_origin

from pydantic import SecretStr
from pydantic.fields import FieldInfo

from workmind.core import config
from workmind.core.config import Settings

ROOT = Path(__file__).resolve().parents[4]
REFERENCE_DIR = ROOT / "docs" / "reference"
CONFIGURATION_PAGE = REFERENCE_DIR / "configuration.md"
OPENAPI_FILE = REFERENCE_DIR / "openapi.json"
CONFIG_SOURCE = Path(config.__file__)

# Sections of the page, in order: (title, field name patterns). A field goes to the first section
# with a matching pattern, else to "Other settings"; within a section, fields keep the order of
# config.py.
SECTIONS: Sequence[tuple[str, Sequence[str]]] = (
    (
        "Deployment",
        ["product_name", "app_env", "public_url", "log_*", "health_check_timeout_s"],
    ),
    (
        "Services",
        [
            "database_url",
            "migrations_database_url",
            "db_app_role",
            "valkey_url",
            "celery_broker_url",
            "qdrant_url",
            "openfga_*",
            "authz_outbox_worker",
        ],
    ),
    ("Sign-in and sessions", ["keycloak_url", "keycloak_realm", "oidc_*", "session_*"]),
    ("Encryption keys", ["secrets_*"]),
    (
        "Language models",
        ["llm_*", "anthropic_api_key", "local_llm_*", "assistant_mode", "agent_fast_path*"],
    ),
    ("Embeddings and search", ["embedding_*", "retrieval_*"]),
    ("Limits", ["max_inflight_per_user", "rate_limits*"]),
    ("Documents and ingestion", ["object_storage_path", "ingestion_mode", "max_upload_bytes"]),
    ("Meetings", ["max_meeting_bytes", "transcription_*"]),
    ("Analytics", ["analytics_*"]),
    (
        "Tools and connectors",
        [
            "searxng_url",
            "mcp_servers",
            "google_client_*",
            "slack_client_*",
            "atlassian_client_*",
            "microsoft_client_*",
            "microsoft_tenant_id",
            "source_*",
        ],
    ),
    ("Email and Slack", ["smtp_*", "slack_*"]),
    ("Enterprise edition", ["license_*", "deployment_id"]),
    ("Demo data (development only)", ["keycloak_admin_*", "demo_password"]),
)
OTHER_SECTION = "Other settings"

# Descriptions for people who run WorkMind. A setting without one here is described by its
# comment in config.py (tests/test_docs_reference.py checks that every key here is a setting).
NOTES: dict[str, str] = {
    "product_name": "The product name in the API's title and in logs.",
    "app_env": (
        "Where this deployment runs. `production` refuses the development values of "
        "`OIDC_CLIENT_SECRET`, `OPENFGA_API_KEY` and `SECRETS_KEY`, and turns off the "
        "interactive API docs; every value except `dev` and `test` requires "
        "`SECRETS_ENCRYPTION_KEY`; development licenses are trusted only in `dev`."
    ),
    "public_url": (
        "The URL people type into the browser. Login redirects and cookie security derive "
        "from it: with `https://`, the session cookie is `Secure` and named `__Host-wm_session`."
    ),
    "log_level": "The lowest level that is logged (`DEBUG`, `INFO`, `WARNING`, `ERROR`).",
    "log_format": (
        "`console` for people, `json` for log collectors. Both carry the same fields "
        "(timestamp, level, logger, request id)."
    ),
    "health_check_timeout_s": (
        "Time limit of each component check in `/readyz` and `/api/v1/system/status`, in seconds."
    ),
    "database_url": (
        "PostgreSQL as the runtime role: no `BYPASSRLS`, data rights only, so row-level "
        "security applies to every query (ADR 0005)."
    ),
    "migrations_database_url": (
        "PostgreSQL as the owner role, used only by Alembic migrations (the `migrate` service)."
    ),
    "db_app_role": "Name of the runtime role; migrations grant and revoke its privileges.",
    "valkey_url": "Valkey (or Redis): sessions, caches and the event streams of answers.",
    "celery_broker_url": "The Celery broker (a Valkey database) for background jobs.",
    "qdrant_url": "Qdrant, which holds the search index of document chunks.",
    "qdrant_api_key": (
        "Qdrant's API key (its `QDRANT__SERVICE__API_KEY`), sent with every call. Compose "
        "leaves it unset (Qdrant listens on 127.0.0.1); the Helm chart generates one."
    ),
    "openfga_api_url": "OpenFGA, which answers permission checks (the second lock).",
    "openfga_api_key": ("Pre-shared key for OpenFGA. Production refuses the development value."),
    "authz_outbox_worker": (
        "Copy permission changes from PostgreSQL to OpenFGA in the background (the outbox). "
        "Off only in unit tests."
    ),
    "keycloak_url": (
        "Keycloak's base URL as the API reaches it, for OpenID Connect discovery and tokens."
    ),
    "keycloak_realm": "The Keycloak realm that holds the logins.",
    "oidc_client_id": "The OpenID Connect client the API signs people in with (ADR 0003).",
    "oidc_client_secret": (
        "That client's secret, shared with Keycloak. Production refuses the development value."
    ),
    "session_idle_timeout_s": "A session ends after this many seconds without a request.",
    "session_absolute_timeout_s": (
        "A session ends this many seconds after sign-in, however active it is."
    ),
    "secrets_key": (
        "Encrypts connector credentials at rest (AES-256-GCM): 32 random bytes, base64. "
        "Production refuses the development value."
    ),
    "secrets_encryption_key": (
        "Encrypts organizations' model provider keys at rest (AES-256-GCM): 32 random bytes, "
        "base64. Required unless `APP_ENV` is `dev` or `test`. "
        "[Rotating it](../models.md#rotating-the-key)."
    ),
    "secrets_encryption_previous_keys": (
        "Older keys, comma-separated, that only decrypt: set during a rotation, then run "
        "`wm secrets rotate`."
    ),
    "llm_provider": (
        "The deployment's model provider: `anthropic`, or `openai_compatible` for any "
        "`/v1/chat/completions` server (Ollama, vLLM, llama.cpp, LM Studio, gateways)."
    ),
    "anthropic_api_key": (
        "API key for the `anthropic` provider. Without a key (here or an organization's own in "
        "Admin > Models), search works but answers do not."
    ),
    "llm_base_url": "The `openai_compatible` server.",
    "llm_api_key": "API key for `LLM_BASE_URL`, if the server needs one.",
    "llm_model": "The default model for every role.",
    "llm_model_answer": "Model for the `answer` role (cited answers). Unset: `LLM_MODEL`.",
    "llm_model_router": (
        "Model for the `router` role (rewriting questions, planning). Unset: `LLM_MODEL`."
    ),
    "llm_model_judge": "Model for the `judge` role (evaluations). Unset: `LLM_MODEL`.",
    "llm_model_generator": ("Model for the `generator` role (meeting notes). Unset: `LLM_MODEL`."),
    "llm_model_agent": (
        "Model for the `agent` role (department, research and analytics agents). Unset: "
        "`LLM_MODEL`."
    ),
    "assistant_mode": (
        "`single`: one search and one answer. `agents`: a planner routes every question to "
        "department agents. `auto`: agents only for questions that span several areas. "
        "`single` was chosen by measurement (ADR 0009)."
    ),
    "max_inflight_per_user": (
        "Answers one person may have in progress at once, across chat and research runs, "
        "`/ask`, MCP `ask`, analytics questions and the agent test console. More get 429 "
        "`too_many_requests`. A slot left by a process that died expires after 15 minutes."
    ),
    "rate_limits_enabled": (
        "Request rate limits per person, per tenant and per client address for sign-in "
        "([multi-tenancy](../multi-tenancy.md)). `false` turns every limit off (load tests)."
    ),
    "rate_limits": (
        "Overrides of the default limits as JSON, e.g. "
        '`{"chat": {"per_user": 10}, "mcp": {"per_tenant": 0}}`; 0 removes that limit. '
        "An unknown limit name or field stops startup."
    ),
    "agent_fast_path": ("Skip the planner when one agent's profile clearly matches the question."),
    "agent_fast_path_min_score": (
        "Fast path: the lowest similarity between a question and the best agent's profile "
        "(calibrated for EmbeddingGemma; see the evaluation)."
    ),
    "agent_fast_path_min_margin": (
        "Fast path: how much closer the best agent must be than the second."
    ),
    "llm_fallbacks": (
        "Anthropic models that support it: a request the model declines is re-run on a model "
        "Anthropic picks (server-side `default` fallback)."
    ),
    "llm_external": (
        "Set `true` when `LLM_BASE_URL` is a third-party API rather than a model you run: the "
        "data policy then treats it as external."
    ),
    "local_llm_base_url": (
        "An optional local model (OpenAI-compatible) for organizations whose policy keeps "
        "restricted data off external APIs. Without one, those sources are left out of answers "
        "from external models, and the answer says so."
    ),
    "local_llm_model": "The local model's name.",
    "local_llm_api_key": "The local model server's API key, if it needs one.",
    "embedding_provider": (
        "`openai_compatible`: any `/v1/embeddings` server. `hash`: deterministic vectors "
        "without a model, for tests and CI (search quality is meaningless)."
    ),
    "embedding_base_url": (
        "The embedding server: `make embeddings` serves port 8091 on the host; the "
        "`embeddings` compose profile serves `http://embeddings:8080/v1`."
    ),
    "embedding_api_key": "The embedding server's API key, if it needs one.",
    "embedding_model": (
        "The embedding model. The search index is named after it and its dimensions, so "
        "changing either starts a new index: re-index with `wm reindex <tenant>`."
    ),
    "embedding_dimensions": "Vector size of the embedding model (768 for EmbeddingGemma-300M).",
    "embedding_query_template": "How a search query is presented to the embedding model.",
    "embedding_document_template": "How a chunk is presented to the embedding model.",
    "retrieval_sparse_weight": (
        "Hybrid search: BM25's weight in rank fusion, relative to dense search (1.0). Chosen by "
        "measurement (ADR 0008)."
    ),
    "retrieval_rrf_k": "Hybrid search: the k of reciprocal rank fusion (ADR 0008).",
    "retrieval_min_similarity": (
        "Relevance floor: when the best dense similarity is below it, nothing is sent to a "
        "model and the assistant says it found nothing. Calibrated for EmbeddingGemma; "
        "recalibrate for another embedding model."
    ),
    "object_storage_path": (
        "Directory for original files, parsed documents, recordings and analytics datasets. "
        "Compose mounts `<repo>/.data/objects` there as `/data/objects`."
    ),
    "ingestion_mode": (
        "`celery`: workers index uploads and syncs. `inline`: the API process does it."
    ),
    "max_upload_bytes": "The largest document upload.",
    "max_meeting_bytes": "The largest meeting recording or transcript.",
    "transcription_provider": (
        "Speech to text for meetings: `faster_whisper` on the worker's CPU, "
        "`openai_compatible` for any `/v1/audio/transcriptions` server, or `none` (transcripts "
        "only)."
    ),
    "transcription_model": (
        "A faster-whisper model (`base.en`, `small.en`, `medium.en`: slower and more accurate), "
        "or the server's model."
    ),
    "transcription_base_url": "The transcription server (`openai_compatible` only).",
    "transcription_api_key": "The transcription server's API key, if it needs one.",
    "analytics_max_rows": "Row cap per analytics query.",
    "analytics_timeout_s": "Time limit per analytics query, in seconds.",
    "analytics_workers": (
        "Analytics query worker processes per API container: how many queries run at once. "
        "Split across the container's `WEB_CONCURRENCY` processes (at least one each), so the "
        "container's memory bound is this x `ANALYTICS_WORKER_MEMORY_MB`."
    ),
    "analytics_worker_memory_mb": "A query worker is stopped above this resident memory.",
    "analytics_queries_per_user": (
        "How many of one person's queries run at once, across every API process and pod "
        "(counted in Valkey); more wait."
    ),
    "searxng_url": (
        "A self-hosted SearXNG for the research agent's `web_search` tool. Unset: no web search."
    ),
    "mcp_servers": (
        'Remote MCP servers whose tools agents may use, as JSON: `[{"key": "erp", "url": '
        '"https://erp.example/mcp", "token": "...", "read_only": ["stock"]}]`. A tool not '
        "listed in `read_only` needs approval."
    ),
    "google_client_id": (
        'Google Drive sources: an OAuth client of type "Web application", with the redirect '
        "URI `<PUBLIC_URL>/api/v1/oauth/google/callback`."
    ),
    "google_client_secret": "That OAuth client's secret.",
    "slack_client_id": (
        "Slack sources: the client id of a Slack app whose redirect URL is "
        "`<PUBLIC_URL>/api/v1/oauth/slack/callback` (bot scopes `channels:history`, "
        "`channels:read`, `groups:history`, `groups:read`). Unset: Slack sources take a pasted "
        "bot token instead of **Connect Slack**."
    ),
    "slack_client_secret": "That Slack app's client secret.",
    "source_failure_alert_after": (
        "Failed syncs in a row after which the people who manage a source are notified, once "
        "per failure episode (docs/connectors.md)."
    ),
    "atlassian_client_id": (
        "Confluence sources that connect with OAuth: an OAuth 2.0 (3LO) app in the Atlassian "
        "developer console, with the callback URL `<PUBLIC_URL>/api/v1/oauth/atlassian/callback` "
        "and the scopes in docs/connectors.md. Not needed for sources that use an API token."
    ),
    "atlassian_client_secret": "That Atlassian app's secret.",
    "microsoft_client_id": (
        "SharePoint and OneDrive sources: the application (client) id of a Microsoft Entra app "
        'registration with a "Web" redirect URI `<PUBLIC_URL>/api/v1/oauth/microsoft/callback` '
        "and the delegated Microsoft Graph permissions in docs/connectors.md."
    ),
    "microsoft_client_secret": "A client secret of that app registration.",
    "microsoft_tenant_id": (
        "The Microsoft Entra directory people sign in with: your tenant id or a verified domain "
        "(a single-tenant app), or `organizations` for any work or school account."
    ),
    "smtp_host": (
        "Outgoing email (`send_email`, always after a person's approval, and the notification "
        "emails people choose in Settings). Unset: email is off. "
        "Development uses Mailpit, which delivers nothing: http://localhost:8025."
    ),
    "smtp_port": "The SMTP port.",
    "smtp_from": "The sender of outgoing email.",
    "smtp_username": "SMTP login, if the server needs one.",
    "smtp_password": "SMTP password, if the server needs one.",
    "smtp_starttls": "Upgrade the SMTP connection with STARTTLS.",
    "slack_api_url": (
        "Slack's Web API, for notification direct messages through a tenant's bot token "
        "(Admin > Automation). Point it at a stub to test without Slack."
    ),
    "slack_webhook_prefix": (
        "Every Slack incoming webhook URL a tenant admin saves must start with this, so nobody "
        "can make the server post elsewhere."
    ),
    "license_key": (
        "The Enterprise license token. Unset: `LICENSE_FILE`, else `license.key` in "
        "`/run/workmind` or `<repo>/.workmind`. Without a valid license: Community."
    ),
    "license_file": "A file that holds the license token.",
    "license_dev_public_key": (
        "Public key of the development license signer (raw Ed25519, base64url), trusted only "
        "when `APP_ENV=dev`. Unset: `dev-public.key` where the license is looked for."
    ),
    "license_dev_public_key_file": "A file that holds that key.",
    "deployment_id": (
        "This deployment's id: a license issued for one deployment is valid only where it matches."
    ),
    "keycloak_admin_user": (
        "The Keycloak admin login the seed tool (`make seed`) creates demo logins with."
    ),
    "keycloak_admin_password": "That login's password.",
    "demo_password": "The password of the demo logins that `make seed` creates.",
}

CONFIGURATION_HEADER = """\
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
"""


# --- Configuration reference --------------------------------------------------------------------


def configuration_reference() -> str:
    comments = _field_comments(CONFIG_SOURCE.read_text(encoding="utf-8"))
    sections: dict[str, list[str]] = {title: [] for title, _ in SECTIONS}
    sections[OTHER_SECTION] = []
    for name, field in Settings.model_fields.items():
        sections[_section_of(name)].append(_row(name, field, comments.get(name, "")))
    parts = [CONFIGURATION_HEADER]
    for title, rows in sections.items():
        if not rows:
            continue
        parts.append(f"## {title}\n")
        parts.append("| Variable | Type | Default | Description |\n|---|---|---|---|")
        parts.append("\n".join(rows) + "\n")
    return "\n".join(parts)


def _section_of(name: str) -> str:
    for title, patterns in SECTIONS:
        if any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns):
            return title
    return OTHER_SECTION


def _row(name: str, field: FieldInfo, comment: str) -> str:
    env = _env_name(name, field)
    description = field.description or NOTES.get(name) or comment
    cells = [
        _type_of(field),
        _default_of(field),
        _markdown_text(description) if description else "*Not described yet.*",
    ]
    return f'| <a id="{name}"></a>`{env}` | ' + " | ".join(map(_table_cell, cells)) + " |"


def _env_name(name: str, field: FieldInfo) -> str:
    alias = field.validation_alias if isinstance(field.validation_alias, str) else field.alias
    return (alias or name).upper()


def _type_of(field: FieldInfo) -> str:
    annotation = _without_none(field.annotation)
    if get_origin(annotation) is Literal:
        return "one of " + ", ".join(f"`{value}`" for value in get_args(annotation))
    names: dict[object, str] = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        SecretStr: "*secret*",
        Path: "path",
    }
    kind = names.get(annotation) or ("JSON list" if get_origin(annotation) is list else "value")
    bounds = _bounds(field)
    return f"{kind}, {bounds}" if bounds else kind


def _without_none(annotation: Any) -> Any:
    if get_origin(annotation) in (Union, types.UnionType):
        rest = [arg for arg in get_args(annotation) if arg is not type(None)]
        if len(rest) == 1:
            return rest[0]
    return annotation


def _bounds(field: FieldInfo) -> str:
    """Field(gt=..., ge=..., lt=..., le=...), which pydantic keeps as annotated-types metadata."""
    symbols = {"gt": ">", "ge": "≥", "lt": "<", "le": "≤"}
    limits: list[str] = []
    for item in field.metadata:
        for attribute, symbol in symbols.items():
            value: object = getattr(item, attribute, None)
            if value is not None:
                limits.append(f"{symbol} {value}")
    return ", ".join(limits)


def _default_of(field: FieldInfo) -> str:
    default: object = field.get_default(call_default_factory=True)
    if default is None:
        return "*unset*"
    if isinstance(default, SecretStr):
        return "*development value*"
    if isinstance(default, bool):
        return f"`{str(default).lower()}`"
    if isinstance(default, (list, dict)):
        return f"`{json.dumps(default)}`"
    text = str(default).replace(str(ROOT), "<repo>")
    if not text:
        return '`""`'
    if isinstance(default, int) and field.annotation is int and default >= 1024 * 1024:
        return f"`{text}` ({default / (1024 * 1024):g} MiB)"
    return f"`{text}`"


def _markdown_text(text: str) -> str:
    """Plain text for a table cell: < and * outside `code` must not become HTML or emphasis."""
    pieces = text.split("`")
    for index in range(0, len(pieces), 2):  # even pieces are outside code spans
        pieces[index] = pieces[index].replace("<", "&lt;").replace("*", "\\*")
    return "`".join(pieces)


def _table_cell(text: str) -> str:
    """A pipe would end the cell. Outside code it is escaped; inside code, where a backslash
    would show on the site, the span becomes <code> with the pipe as an entity (GitHub and
    Python-Markdown both render that)."""
    pieces = text.split("`")
    for index, piece in enumerate(pieces):
        if index % 2 == 0:
            pieces[index] = piece.replace("|", "\\|")
        elif "|" in piece:
            pieces[index] = "<code>" + html.escape(piece).replace("|", "&#124;") + "</code>"
        else:
            pieces[index] = f"`{piece}`"
    return "".join(pieces)


def _field_comments(source: str) -> dict[str, str]:
    """Each Settings field's comment in config.py: the comment lines right above it (without a
    `# --- section ---` header) and a comment at the end of its first line."""
    lines = source.splitlines()
    trailing: dict[int, str] = {}
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT and lines[token.start[0] - 1].lstrip()[:1] != "#":
            trailing[token.start[0]] = token.string.lstrip("#").strip()
    settings = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.ClassDef) and node.name == Settings.__name__
    )
    comments: dict[str, str] = {}
    for statement in settings.body:
        if not (isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name)):
            continue
        name, row = statement.target.id, statement.lineno
        above: list[str] = []
        index = row - 2
        while index >= 0 and lines[index].lstrip().startswith("#"):
            above.insert(0, lines[index].strip().lstrip("#").strip())
            index -= 1
        while above and above[0].startswith("---"):  # a section header, up to its closing ---
            closing = next((i for i, text in enumerate(above) if text.endswith("---")), 0)
            above = above[closing + 1 :]
        inline = trailing.get(row, "")
        if inline.upper() == name.upper() or inline.startswith(("noqa", "pyright", "type:")):
            inline = ""
        comments[name] = " ".join([*above, inline]).strip()
    return comments


# --- OpenAPI schema -----------------------------------------------------------------------------


def openapi_schema() -> dict[str, Any]:
    """The API's OpenAPI schema with default settings and no Enterprise extensions."""
    from workmind.core.edition import Edition
    from workmind.core.extensions import ExtensionRegistry
    from workmind.main import create_app

    app = create_app(Settings.model_construct(), extensions=ExtensionRegistry(Edition.community()))
    return app.openapi()


def openapi_json() -> str:
    return json.dumps(openapi_schema(), indent=2, ensure_ascii=False) + "\n"


# --- Command line -------------------------------------------------------------------------------


def generated() -> dict[Path, str]:
    return {CONFIGURATION_PAGE: configuration_reference(), OPENAPI_FILE: openapi_json()}


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    check = args == ["--check"]
    if args and not check:
        print(__doc__, file=sys.stderr)
        return 2
    stale: list[Path] = []
    for path, content in generated().items():
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current == content:
            continue
        stale.append(path)
        if not check:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
    for path in stale:
        verb = "out of date" if check else "written"
        print(f"{path.relative_to(ROOT)}: {verb}", file=sys.stderr)
    if check and stale:
        print("Run `make docs-gen` and commit the result.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
