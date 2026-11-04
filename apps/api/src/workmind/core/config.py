"""Runtime configuration.

Every setting is read from environment variables (or an optional `.env` file). Defaults
target local development, where the API runs on the host and the infrastructure runs in
Docker (`make infra`). Inside docker compose, `compose.yaml` overrides the hostnames.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from workmind import __version__

Environment = Literal["dev", "test", "demo", "production"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

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
    keycloak_url: str = "http://localhost:8480/auth"

    health_check_timeout_s: float = Field(default=2.0, gt=0)

    @property
    def version(self) -> str:
        return __version__

    @property
    def docs_enabled(self) -> bool:
        return self.app_env != "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
