"""Runtime configuration.

Every setting is read from environment variables (or an optional `.env` file). Defaults
target local development, where the API runs on the host and the infrastructure runs in
Docker (`make infra`). Inside docker compose, `compose.yaml` overrides the hostnames.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from workmind import __version__

Environment = Literal["dev", "test", "demo", "production"]
DEV_OIDC_CLIENT_SECRET = "dev-only-oidc-client-secret"  # noqa: S105  (refused in production)
DEV_OPENFGA_API_KEY = "dev-only-openfga-key"  # (refused in production)


class Settings(BaseSettings):
    # The repo-root .env (used by docker compose) also applies when running on the host.
    model_config = SettingsConfigDict(
        env_file=("../../.env", ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    # Product identity. The name lives here so a rename touches config, not code.
    product_name: str = "WorkMind AI"
    app_env: Environment = "dev"

    log_level: str = "INFO"
    log_format: Literal["console", "json"] = "console"

    # Runtime role: no BYPASSRLS, only DML rights (see deploy/postgres/init).
    database_url: str = "postgresql+asyncpg://workmind_app:workmind_app@localhost:5432/workmind"
    # Owner role: used only by Alembic migrations.
    migrations_database_url: str = (
        "postgresql+asyncpg://workmind_owner:workmind_owner@localhost:5432/workmind"
    )
    # Name of the runtime role; migrations grant and revoke privileges for it.
    db_app_role: str = "workmind_app"
    valkey_url: str = "redis://localhost:6379/0"
    qdrant_url: str = "http://localhost:6333"
    openfga_api_url: str = "http://localhost:8401"
    openfga_api_key: SecretStr = SecretStr(DEV_OPENFGA_API_KEY)
    # Background sync of the authorization outbox (disabled in unit tests).
    authz_outbox_worker: bool = True
    keycloak_url: str = "http://localhost:8480/auth"
    keycloak_realm: str = "workmind"

    # The URL users type into the browser. Login redirects and cookie security derive from it.
    public_url: str = "http://localhost:8480"
    oidc_client_id: str = "workmind-api"
    oidc_client_secret: SecretStr = SecretStr(DEV_OIDC_CLIENT_SECRET)

    # Development seeding only (python -m workmind.seed).
    keycloak_admin_user: str = "admin"
    keycloak_admin_password: SecretStr = SecretStr("admin")
    demo_password: SecretStr = SecretStr("workmind-demo")

    health_check_timeout_s: float = Field(default=2.0, gt=0)

    # --- Language models (ADR 0007). Every role defaults to llm_model; override per role. ---
    llm_provider: Literal["anthropic", "openai_compatible"] = "anthropic"
    anthropic_api_key: SecretStr | None = None  # ANTHROPIC_API_KEY
    llm_base_url: str = "http://localhost:11434/v1"  # openai_compatible: Ollama, vLLM, llama.cpp
    llm_api_key: SecretStr | None = None
    llm_model: str = "claude-opus-5-5"
    llm_model_answer: str | None = None
    llm_model_router: str | None = None
    llm_model_judge: str | None = None
    llm_model_generator: str | None = None
    llm_fallbacks: bool = True  # Anthropic server-side refusal fallback ("default" routing)

    # --- Embeddings: any OpenAI-compatible /v1/embeddings server; "hash" for tests. ---
    embedding_provider: Literal["openai_compatible", "hash"] = "openai_compatible"
    embedding_base_url: str = "http://localhost:8091/v1"  # llama-server (make embeddings)
    embedding_api_key: SecretStr | None = None
    embedding_model: str = "embeddinggemma-300m"
    embedding_dimensions: int = Field(default=768, gt=0)
    embedding_query_template: str = "task: search result | query: {text}"
    embedding_document_template: str = "title: {title} | text: {text}"

    # --- Ingestion ---
    # Shared with the containers through a bind mount of <repo>/.data/objects (compose.yaml).
    object_storage_path: str = str(Path(__file__).resolve().parents[5] / ".data" / "objects")
    celery_broker_url: str = "redis://localhost:6379/1"
    ingestion_mode: Literal["celery", "inline"] = "celery"  # inline: in the API process
    max_upload_bytes: int = Field(default=50 * 1024 * 1024, gt=0)

    # Sessions (ARCHITECTURE section 6.3): sliding idle timeout, hard absolute lifetime.
    session_idle_timeout_s: int = Field(default=1800, gt=0)
    session_absolute_timeout_s: int = Field(default=43200, gt=0)

    @model_validator(mode="after")
    def _no_dev_secrets_in_production(self) -> Self:
        if self.app_env != "production":
            return self
        if self.oidc_client_secret.get_secret_value() == DEV_OIDC_CLIENT_SECRET:
            raise ValueError("OIDC_CLIENT_SECRET must be set in production")
        if self.openfga_api_key.get_secret_value() == DEV_OPENFGA_API_KEY:
            raise ValueError("OPENFGA_API_KEY must be set in production")
        return self

    @property
    def version(self) -> str:
        return __version__

    @property
    def docs_enabled(self) -> bool:
        return self.app_env != "production"

    @property
    def qdrant_collection(self) -> str:
        """One collection per embedding model: switching models builds a new one (ARCH. 8)."""
        slug = "".join(c if c.isalnum() else "_" for c in self.embedding_model.lower())
        return f"chunks_{slug}_{self.embedding_dimensions}"

    @property
    def public_origin(self) -> str:
        parts = urlsplit(self.public_url)
        return f"{parts.scheme}://{parts.netloc}"

    @property
    def secure_cookies(self) -> bool:
        return self.public_url.startswith("https://")

    @property
    def session_cookie_name(self) -> str:
        # The __Host- prefix makes browsers refuse the cookie unless Secure, Path=/, no Domain.
        return "__Host-wm_session" if self.secure_cookies else "wm_session"

    @property
    def oidc_redirect_uri(self) -> str:
        return f"{self.public_url.rstrip('/')}/api/auth/callback"

    @property
    def oidc_discovery_url(self) -> str:
        return f"{self.keycloak_url}/realms/{self.keycloak_realm}/.well-known/openid-configuration"


@lru_cache
def get_settings() -> Settings:
    return Settings()
