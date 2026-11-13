"""OAuth for sources: the identity providers' endpoints and scopes, the apps the operator
registered (settings), and access tokens renewed from a stored refresh token.

Tokens live only in a source's encrypted credentials (secrets.py). Atlassian and Microsoft
rotate refresh tokens: each renewal returns a new one, which the connector hands back
(`updated_credentials`) so the sync engine stores it in place of the old one.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Self

import httpx
from pydantic import SecretStr

from workmind.core.config import Settings
from workmind.modules.connectors.base import ConnectorError
from workmind.modules.connectors.transport import Sleep, send

# --- Atlassian (Confluence Cloud), OAuth 2.0 authorization code grant ("3LO") ---------------
ATLASSIAN_AUTHORIZE = "https://auth.atlassian.com/authorize"
ATLASSIAN_TOKEN = "https://auth.atlassian.com/oauth/token"  # noqa: S105  (an endpoint)
ATLASSIAN_RESOURCES = "https://api.atlassian.com/oauth/token/accessible-resources"
ATLASSIAN_API = "https://api.atlassian.com/ex/confluence"
# Granular scopes of the Confluence REST API v2 calls the connector makes (spaces, pages,
# page attachments and their download), plus offline_access for a refresh token.
ATLASSIAN_SCOPES = (
    "read:space:confluence",
    "read:page:confluence",
    "read:attachment:confluence",
    "offline_access",
)

# --- Microsoft identity platform (SharePoint, OneDrive), authorization code + PKCE ----------
MICROSOFT_LOGIN = "https://login.microsoftonline.com"
TENANT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,99}$")  # a GUID, a domain, or a keyword


def microsoft_authorize_url(tenant: str) -> str:
    return f"{MICROSOFT_LOGIN}/{tenant}/oauth2/v2.0/authorize"


def microsoft_token_url(tenant: str) -> str:
    return f"{MICROSOFT_LOGIN}/{tenant}/oauth2/v2.0/token"


def microsoft_scopes(config: Mapping[str, Any]) -> tuple[str, ...]:
    """The least the source needs (Microsoft Graph permissions reference, delegated):
    a SharePoint site's libraries need Sites.Read.All; a drive named by id may be anyone's,
    so Files.Read.All; the connecting account's own OneDrive, Files.Read."""
    if config.get("site_url"):
        needed = "Sites.Read.All"
    elif config.get("library"):
        needed = "Files.Read.All"
    else:
        needed = "Files.Read"
    return ("offline_access", needed)


def _secret(value: SecretStr | None) -> str | None:
    return (value.get_secret_value() or None) if value is not None else None


@dataclass(frozen=True)
class OAuthApp:
    """An OAuth client the operator registered with an identity provider."""

    client_id: str | None = None
    client_secret: str | None = None
    tenant: str = "organizations"  # Microsoft only: the directory (tenant id or domain)

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)


@dataclass(frozen=True)
class OAuthApps:
    google: OAuthApp = OAuthApp()
    slack: OAuthApp = OAuthApp()
    atlassian: OAuthApp = OAuthApp()
    microsoft: OAuthApp = OAuthApp()

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        return cls(
            google=OAuthApp(settings.google_client_id, _secret(settings.google_client_secret)),
            slack=OAuthApp(settings.slack_client_id, _secret(settings.slack_client_secret)),
            atlassian=OAuthApp(
                settings.atlassian_client_id, _secret(settings.atlassian_client_secret)
            ),
            microsoft=OAuthApp(
                settings.microsoft_client_id,
                _secret(settings.microsoft_client_secret),
                tenant=settings.microsoft_tenant_id,
            ),
        )


def expires_at(body: Mapping[str, Any], now: datetime | None = None) -> str:
    seconds = body.get("expires_in", 3600)
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        seconds = 3600
    return ((now or datetime.now(UTC)) + timedelta(seconds=seconds)).isoformat()


class RefreshingToken:
    """A bearer token, renewed with the refresh token shortly before it expires (or after the
    service refused it). `updated` holds the credentials to store after a renewal."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        credentials: Mapping[str, Any],
        *,
        token_url: str,
        app: OAuthApp,
        service: str,
        reconnect: str,
        as_json: bool,
        extra: Mapping[str, str] | None = None,
        sleep: Sleep,
    ) -> None:
        self._http = http
        self._credentials = dict(credentials)
        self._token_url = token_url
        self._app = app
        self._service = service
        self._reconnect = reconnect
        self._as_json = as_json
        self._extra = dict(extra or {})
        self._sleep = sleep
        self.updated: dict[str, Any] | None = None

    def _fresh(self) -> bool:
        value = self._credentials.get("expires_at")
        if not (self._credentials.get("access_token") and isinstance(value, str)):
            return False
        try:
            expires = datetime.fromisoformat(value)
        except ValueError:
            return False
        return expires > datetime.now(UTC) + timedelta(minutes=2)

    def expire(self) -> None:
        """The service refused the token: the next call renews it."""
        self._credentials.pop("expires_at", None)

    async def get(self) -> str:
        if self._fresh():
            return str(self._credentials["access_token"])
        refresh = self._credentials.get("refresh_token")
        if not (self._app.configured and refresh):
            raise ConnectorError(f"the {self._service} access token expired; {self._reconnect}")
        payload = {
            **self._extra,
            "grant_type": "refresh_token",
            "refresh_token": str(refresh),
            "client_id": self._app.client_id or "",
            "client_secret": self._app.client_secret or "",
        }
        response = await send(
            self._http,
            "POST",
            self._token_url,
            service=self._service,
            sleep=self._sleep,
            json=payload if self._as_json else None,
            data=None if self._as_json else payload,
            timeout_s=20,
        )
        if response.is_error:
            raise ConnectorError(f"{self._service} refused the refresh token; {self._reconnect}")
        try:
            body: dict[str, Any] = response.json()
        except ValueError as exc:
            raise ConnectorError(f"{self._service} sent an unreadable token response") from exc
        access = body.get("access_token")
        if not isinstance(access, str) or not access:
            raise ConnectorError(f"{self._service} sent no access token; {self._reconnect}")
        self._credentials["access_token"] = access
        if isinstance(body.get("refresh_token"), str) and body["refresh_token"]:
            self._credentials["refresh_token"] = body["refresh_token"]  # rotated
        self._credentials["expires_at"] = expires_at(body)
        self.updated = dict(self._credentials)
        return access


# --- Connecting: the authorization code for tokens --------------------------------------------


async def _token_request(
    http: httpx.AsyncClient,
    url: str,
    service: str,
    *,
    json: Mapping[str, str] | None = None,
    data: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    try:
        response = await http.post(
            url, json=json, data=dict(data) if data is not None else None, timeout=20
        )
    except httpx.HTTPError as exc:
        raise ConnectorError(f"{service} is unreachable") from exc
    if response.is_error:
        raise ConnectorError(f"{service} refused the authorization code")
    try:
        body = response.json()
    except ValueError as exc:
        raise ConnectorError(f"{service} sent an unreadable token response") from exc
    if not isinstance(body, dict) or not isinstance(body.get("access_token"), str):  # pyright: ignore[reportUnknownMemberType]
        raise ConnectorError(f"{service} sent no access token")
    return body  # pyright: ignore[reportUnknownVariableType]


def _cloud_id(resources: object, site_url: str) -> str | None:
    """The id of the configured site among the sites the person granted."""
    wanted = site_url.rstrip("/").lower()
    for resource in resources if isinstance(resources, list) else []:  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(resource, dict):
            continue
        url = str(resource.get("url", "")).rstrip("/").lower()  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        if url == wanted and resource.get("id"):  # pyright: ignore[reportUnknownMemberType]
            return str(resource["id"])  # pyright: ignore[reportUnknownArgumentType]
    return None


async def atlassian_connect(
    http: httpx.AsyncClient, app: OAuthApp, *, code: str, redirect_uri: str, site_url: str
) -> dict[str, Any]:
    """Tokens for the authorization code, plus the cloud id of the configured site (an
    Atlassian app is granted per site, and API calls name the site by that id)."""
    body = await _token_request(
        http,
        ATLASSIAN_TOKEN,
        "Atlassian",
        json={
            "grant_type": "authorization_code",
            "client_id": app.client_id or "",
            "client_secret": app.client_secret or "",
            "code": code,
            "redirect_uri": redirect_uri,
        },
    )
    try:
        response = await http.get(
            ATLASSIAN_RESOURCES,
            headers={
                "Authorization": f"Bearer {body['access_token']}",
                "Accept": "application/json",
            },
            timeout=20,
        )
        resources: object = response.json() if not response.is_error else []
    except (httpx.HTTPError, ValueError) as exc:
        raise ConnectorError("Atlassian did not list the granted sites") from exc
    cloud_id = _cloud_id(resources, site_url)
    if cloud_id is None:
        raise ConnectorError(f"the account did not grant access to {site_url}")
    return {
        "access_token": body["access_token"],
        "refresh_token": str(body.get("refresh_token") or ""),
        "expires_at": expires_at(body),
        "cloud_id": cloud_id,
        "site_url": site_url,
    }


async def microsoft_connect(
    http: httpx.AsyncClient,
    app: OAuthApp,
    *,
    code: str,
    redirect_uri: str,
    verifier: str,
    scope: str,
) -> dict[str, Any]:
    """Tokens for the authorization code (with the PKCE verifier of this flow)."""
    body = await _token_request(
        http,
        microsoft_token_url(app.tenant),
        "Microsoft",
        data={
            "grant_type": "authorization_code",
            "client_id": app.client_id or "",
            "client_secret": app.client_secret or "",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
            "scope": scope,
        },
    )
    return {
        "access_token": body["access_token"],
        "refresh_token": str(body.get("refresh_token") or ""),
        "expires_at": expires_at(body),
        "scope": scope,  # renewals ask for the same permissions
    }
