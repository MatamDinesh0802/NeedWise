"""From a source's configuration to a connector. Each kind validates its own configuration
(non-secret) and names the credentials it needs (secret, stored encrypted)."""

from collections.abc import Mapping
from typing import Any

import httpx
from pydantic import BaseModel, Field, HttpUrl, field_validator

from workmind.core.urls import check_url
from workmind.modules.connectors.base import Connector, ConnectorError
from workmind.modules.connectors.gdrive import GoogleDriveConnector
from workmind.modules.connectors.github import GitHubConnector
from workmind.modules.connectors.models import Source
from workmind.modules.connectors.slack import SlackConnector
from workmind.modules.connectors.web import WebConnector


class WebConfig(BaseModel):
    start_urls: list[HttpUrl] = Field(min_length=1, max_length=20)
    max_pages: int = Field(default=100, ge=1, le=5000)
    path_prefix: str | None = None

    @field_validator("start_urls")
    @classmethod
    def _fetchable(cls, urls: list[HttpUrl]) -> list[HttpUrl]:
        """The outbound URL policy (core/urls.py); the crawler checks resolved addresses too."""
        for url in urls:
            check_url(str(url))  # UnsafeUrlError is a ValueError: a 422 with the reason
        return urls


class GitHubConfig(BaseModel):
    repo: str = Field(pattern=r"^[\w.-]+/[\w.-]+$")
    branch: str | None = None
    paths: list[str] = [""]


class SlackConfig(BaseModel):
    channels: list[str] = Field(min_length=1, max_length=100, description="channel ids, e.g. C0123")
    days: int = Field(default=90, ge=1, le=3650)


class GoogleDriveConfig(BaseModel):
    folder_id: str = Field(min_length=5)


CONFIGS: dict[str, type[BaseModel]] = {
    "web": WebConfig,
    "github": GitHubConfig,
    "slack": SlackConfig,
    "gdrive": GoogleDriveConfig,
}
CREDENTIALS: dict[str, tuple[str, ...]] = {
    "web": (),
    "github": ("token",),  # optional for public repositories
    "slack": ("bot_token",),
    "gdrive": ("refresh_token",),  # set by the OAuth flow
}


def validate_config(kind: str, config: Mapping[str, Any]) -> dict[str, Any]:
    model = CONFIGS.get(kind)
    if model is None:
        raise ValueError(f"unknown source kind {kind!r}")
    return model.model_validate(config).model_dump(mode="json")


def build_connector(
    source: Source,
    credentials: Mapping[str, Any],
    *,
    http: httpx.AsyncClient,
    google_client: tuple[str | None, str | None] = (None, None),
) -> Connector:
    config = dict(source.config)
    match source.kind:
        case "web":
            c = WebConfig.model_validate(config)
            return WebConnector(
                http,
                start_urls=[str(u) for u in c.start_urls],
                max_pages=c.max_pages,
                path_prefix=c.path_prefix,
            )
        case "github":
            c = GitHubConfig.model_validate(config)
            return GitHubConnector(
                http, repo=c.repo, branch=c.branch, paths=c.paths, token=credentials.get("token")
            )
        case "slack":
            c = SlackConfig.model_validate(config)
            return SlackConnector(
                http, token=str(credentials.get("bot_token", "")), channels=c.channels, days=c.days
            )
        case "gdrive":
            c = GoogleDriveConfig.model_validate(config)
            client_id, client_secret = google_client
            return GoogleDriveConnector(
                http,
                folder_id=c.folder_id,
                credentials=credentials,
                client_id=client_id,
                client_secret=client_secret,
            )
        case _:
            raise ConnectorError(f"unknown source kind {source.kind!r}")
