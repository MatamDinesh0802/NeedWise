"""From a source's configuration to a connector. Each kind validates its own configuration
(non-secret) and names the credentials it needs (secret, stored encrypted).

`KINDS` describes every kind for people and for the admin UI (`GET /admin/sources/kinds`):
its name, how it signs in, the credential fields it takes and its configuration's JSON schema.
A new connector adds an entry there and a case in `build_connector`.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field, HttpUrl, field_validator

from workmind.core.urls import check_url
from workmind.modules.connectors.base import Connector, ConnectorError
from workmind.modules.connectors.confluence import ConfluenceConnector
from workmind.modules.connectors.gdrive import GoogleDriveConnector
from workmind.modules.connectors.github import GitHubConnector
from workmind.modules.connectors.models import Source
from workmind.modules.connectors.oauth import OAuthApps
from workmind.modules.connectors.sharepoint import SharePointConnector
from workmind.modules.connectors.slack import SlackConnector
from workmind.modules.connectors.web import WebConnector

ATLASSIAN_SITE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}\.atlassian\.net$")
SHAREPOINT_HOST = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}\.sharepoint\.com$")  # also <t>-my
SPACE_KEY = re.compile(r"^~?[A-Za-z0-9_:-]{1,255}$")


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


def _https_origin(value: str, host_pattern: re.Pattern[str], example: str) -> tuple[str, str]:
    parts = urlsplit(value.strip())
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host_pattern.match(host) or parts.port or parts.username:
        raise ValueError(f"use your site's https address, like {example}")
    return f"https://{host}", parts.path


class ConfluenceConfig(BaseModel):
    site_url: str = Field(description="Your Confluence Cloud site, e.g. https://acme.atlassian.net")
    spaces: list[str] = Field(
        min_length=1, max_length=50, description="Space keys, e.g. ENG (one collection for all)"
    )
    include_attachments: bool = Field(
        default=True, description="Also sync the PDF, Office and text files attached to pages"
    )

    @field_validator("site_url")
    @classmethod
    def _site(cls, value: str) -> str:
        origin, _ = _https_origin(value, ATLASSIAN_SITE, "https://acme.atlassian.net")
        return origin  # "/wiki" and anything after it are not part of the site

    @field_validator("spaces")
    @classmethod
    def _keys(cls, keys: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(k.strip() for k in keys))
        bad = [k for k in cleaned if not SPACE_KEY.match(k)]
        if bad:
            raise ValueError(f"not a space key: {bad[0]!r} (the key is in the space's URL)")
        return cleaned


class SharePointConfig(BaseModel):
    site_url: str | None = Field(
        default=None,
        description=(
            "A SharePoint site, e.g. https://contoso.sharepoint.com/sites/finance. "
            "Empty: the OneDrive of the account that connects."
        ),
    )
    library: str | None = Field(
        default=None,
        description="Document library name (or drive id); empty: the site's default library",
    )
    folder: str | None = Field(
        default=None, description="A folder inside the library, e.g. Policies/2026; empty: all"
    )

    @field_validator("site_url")
    @classmethod
    def _site(cls, value: str | None) -> str | None:
        if not value or not value.strip():
            return None
        origin, path = _https_origin(value, SHAREPOINT_HOST, "https://contoso.sharepoint.com")
        path = path.rstrip("/")
        if any(segment in ("..", ".") for segment in path.split("/")):
            raise ValueError("the site path cannot contain . or ..")
        return origin + path

    @field_validator("library", "folder")
    @classmethod
    def _plain(cls, value: str | None) -> str | None:
        if value is None or not value.strip("/ "):
            return None
        value = value.strip().strip("/")
        if any(s in ("..", ".", "") for s in value.split("/")) or len(value) > 400:
            raise ValueError("use a plain path, like Policies/2026")
        return value


AuthKind = Literal["none", "token", "oauth", "oauth_or_token"]


@dataclass(frozen=True)
class CredentialField:
    name: str
    label: str
    hint: str = ""
    optional: bool = False


@dataclass(frozen=True)
class KindInfo:
    """What a connector kind is, for people and for a generic admin form."""

    kind: str
    label: str
    description: str
    config: type[BaseModel]
    auth: AuthKind
    # Fields an admin types (stored encrypted, never returned). OAuth fills its own.
    credentials: tuple[CredentialField, ...] = ()
    oauth: Literal["google", "slack", "atlassian", "microsoft"] | None = None


KINDS: dict[str, KindInfo] = {
    info.kind: info
    for info in (
        KindInfo(
            "web",
            "Website",
            "Crawls pages from start URLs within their site (respects robots.txt).",
            WebConfig,
            "none",
        ),
        KindInfo(
            "github",
            "GitHub",
            "Markdown, text and PDF files from a repository branch.",
            GitHubConfig,
            "token",
            (
                CredentialField(
                    "token",
                    "Access token",
                    "Only for private repositories; read-only contents access is enough.",
                    optional=True,
                ),
            ),
        ),
        KindInfo(
            "slack",
            "Slack",
            "Channel history, one document per channel and month (always trust: discussion).",
            SlackConfig,
            "oauth_or_token",
            (
                CredentialField(
                    "bot_token",
                    "Bot token",
                    "xoxb-… with channels:history and channels:read; or connect with Slack.",
                ),
            ),
            oauth="slack",
        ),
        KindInfo(
            "gdrive",
            "Google Drive",
            "A folder and its subfolders; Docs, Sheets and Slides are exported.",
            GoogleDriveConfig,
            "oauth",
            oauth="google",
        ),
        KindInfo(
            "confluence",
            "Confluence",
            "Pages of Confluence Cloud spaces, with their attachments and page tree.",
            ConfluenceConfig,
            "oauth_or_token",
            (
                CredentialField(
                    "email",
                    "Account email",
                    "With an API token instead of connecting with OAuth.",
                    optional=True,
                ),
                CredentialField(
                    "api_token",
                    "API token",
                    "From id.atlassian.com > Security > API tokens; read access is enough.",
                    optional=True,
                ),
            ),
            oauth="atlassian",
        ),
        KindInfo(
            "sharepoint",
            "SharePoint / OneDrive",
            "Files of a SharePoint document library or a OneDrive, or of one folder in it.",
            SharePointConfig,
            "oauth",
            oauth="microsoft",
        ),
    )
}
CONFIGS: dict[str, type[BaseModel]] = {k: info.config for k, info in KINDS.items()}
# Credential fields each kind expects (`needs` in the API). OAuth kinds name the field the
# OAuth flow sets.
CREDENTIALS: dict[str, tuple[str, ...]] = {
    "web": (),
    "github": ("token",),  # optional for public repositories
    "slack": ("bot_token",),
    "gdrive": ("refresh_token",),  # set by the OAuth flow
    "confluence": ("email", "api_token"),  # or OAuth, which sets a refresh token
    "sharepoint": ("refresh_token",),  # set by the OAuth flow
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
    apps: OAuthApps | None = None,
) -> Connector:
    config = dict(source.config)
    apps = apps or OAuthApps()
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
            return GoogleDriveConnector(
                http,
                folder_id=c.folder_id,
                credentials=credentials,
                client_id=apps.google.client_id,
                client_secret=apps.google.client_secret,
            )
        case "confluence":
            c = ConfluenceConfig.model_validate(config)
            return ConfluenceConnector(
                http,
                site_url=c.site_url,
                spaces=c.spaces,
                include_attachments=c.include_attachments,
                credentials=credentials,
                app=apps.atlassian,
            )
        case "sharepoint":
            c = SharePointConfig.model_validate(config)
            return SharePointConnector(
                http,
                site_url=c.site_url,
                library=c.library,
                folder=c.folder,
                credentials=credentials,
                app=apps.microsoft,
            )
        case _:
            raise ConnectorError(f"unknown source kind {source.kind!r}")
