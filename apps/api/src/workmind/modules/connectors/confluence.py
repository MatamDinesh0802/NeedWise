"""Confluence Cloud: the pages of one or more spaces, and their attachments, through the REST
API v2 (https://developer.atlassian.com/cloud/confluence/rest/v2/).

Two ways to sign in:
- OAuth 2.0 (3LO): an admin connects from Admin > Sources. WorkMind stores the refresh token
  and the site's cloud id (encrypted) and calls https://api.atlassian.com/ex/confluence/<id>.
- An API token with the account's email: Basic auth against https://<site>.atlassian.net.

A scan lists every current page of the configured spaces with its version number, without
bodies, so the sync engine fetches only pages whose version changed. The listing is complete:
a page that was deleted, archived or moved to another space disappears from WorkMind. A page
becomes a Markdown document converted from its storage format; its attachments WorkMind can
parse (PDF, Office, text) become documents of their own. Where a page sits (space, ancestor
pages) is the document's source metadata, and its link opens the page in Confluence.
"""

import asyncio
import base64
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from workmind.core.urls import UnsafeUrlError, check_url
from workmind.modules.connectors.base import ConnectorError, RemoteItem, ScanResult
from workmind.modules.connectors.confluence_storage import storage_to_markdown
from workmind.modules.connectors.oauth import (
    ATLASSIAN_API,
    ATLASSIAN_TOKEN,
    OAuthApp,
    RefreshingToken,
)
from workmind.modules.connectors.transport import REDIRECTS, Sleep, send

SERVICE = "Confluence"
LIMIT = "250"  # the largest page size the v2 list endpoints accept
MAX_PAGES = 10_000  # of results, per listing: a guard against a cursor that never ends
READABLE = {".pdf", ".docx", ".xlsx", ".pptx", ".md", ".txt", ".html", ".htm", ".csv"}
_UNSAFE_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


@dataclass
class _Page:
    id: str
    title: str
    parent: str | None
    version: int
    created: str | None
    webui: str


def _filename(title: str, fallback: str) -> str:
    name = _UNSAFE_NAME.sub(" ", title).strip(" .")[:150] or fallback
    return f"{name}.md"


def _when(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class ConfluenceConnector:
    kind = "confluence"

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        site_url: str,
        spaces: list[str],
        include_attachments: bool = True,
        credentials: Mapping[str, Any],
        app: OAuthApp,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._http = http
        self._site = site_url.rstrip("/")
        self._spaces = spaces
        self._attachments = include_attachments
        self._sleep = sleep
        self._token: RefreshingToken | None = None
        self._basic: str | None = None
        if credentials.get("api_token"):
            email = str(credentials.get("email", "")).strip()
            if not email:
                raise ConnectorError("a Confluence API token needs the account's email")
            pair = f"{email}:{credentials['api_token']}".encode()
            self._basic = "Basic " + base64.b64encode(pair).decode()
            self._base = self._site
        elif credentials.get("refresh_token") or credentials.get("access_token"):
            cloud_id = str(credentials.get("cloud_id", ""))
            if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", cloud_id):
                raise ConnectorError("reconnect Confluence: the site's cloud id is missing")
            self._base = f"{ATLASSIAN_API}/{cloud_id}"
            self._token = RefreshingToken(
                http,
                credentials,
                token_url=ATLASSIAN_TOKEN,
                app=app,
                service=SERVICE,
                reconnect="reconnect Confluence",
                as_json=True,
                sleep=sleep,
            )
        else:
            raise ConnectorError("connect Confluence (OAuth) or set an email and API token")

    @property
    def updated_credentials(self) -> dict[str, Any] | None:
        return self._token.updated if self._token else None

    # --- HTTP ---------------------------------------------------------------------------------

    async def _authorization(self) -> str:
        if self._basic:
            return self._basic
        assert self._token is not None  # noqa: S101  (one of the two is set in __init__)
        return f"Bearer {await self._token.get()}"

    async def _send(self, url: str, params: Mapping[str, str] | None) -> httpx.Response:
        return await send(
            self._http,
            "GET",
            url,
            service=SERVICE,
            sleep=self._sleep,
            headers={"Authorization": await self._authorization(), "Accept": "application/json"},
            params=params,
        )

    async def _call(self, url: str, params: Mapping[str, str] | None = None) -> httpx.Response:
        response = await self._send(url, params)
        if response.status_code == 401 and self._token:
            self._token.expire()  # revoked or expired early: renew once
            response = await self._send(url, params)
        if response.status_code == 401:
            raise ConnectorError("Confluence refused the credentials; reconnect or check the token")
        if response.status_code == 403:
            raise ConnectorError("Confluence denied access; check the account can read the space")
        if response.status_code == 404:
            raise ConnectorError(f"Confluence: not found ({urlsplit(url).path})")
        if response.is_error:
            raise ConnectorError(f"Confluence answered HTTP {response.status_code}")
        return response

    async def _json(self, url: str, params: Mapping[str, str] | None = None) -> dict[str, Any]:
        try:
            body = (await self._call(url, params)).json()
        except ValueError as exc:
            raise ConnectorError("Confluence sent a response that is not JSON") from exc
        if not isinstance(body, dict):
            raise ConnectorError("Confluence sent an unexpected response")
        return body  # pyright: ignore[reportUnknownVariableType]

    async def _list(self, path: str, params: Mapping[str, str]) -> list[dict[str, Any]]:
        """Every result of a v2 list endpoint, following its cursor links (`_links.next`)."""
        results: list[dict[str, Any]] = []
        url, query = f"{self._base}{path}", dict(params)
        for _ in range(MAX_PAGES):
            body = await self._json(url, query)
            results += [r for r in body.get("results", []) if isinstance(r, dict)]
            links: dict[str, Any] = body.get("_links") or {}
            following = links.get("next")
            if not isinstance(following, str) or not following:
                return results
            # The link is relative to the site ("/wiki/api/v2/...?cursor=..."): keep only its
            # path and query, on our own base, so a link can never send credentials elsewhere.
            parts = urlsplit(following)
            if not parts.path.startswith("/wiki/api/v2/"):
                raise ConnectorError("Confluence sent an unexpected pagination link")
            url, query = f"{self._base}{parts.path}?{parts.query}", {}
        raise ConnectorError("Confluence kept paging; the listing was stopped")

    # --- scan ---------------------------------------------------------------------------------

    async def _resolve_spaces(self) -> list[dict[str, Any]]:
        spaces = await self._list(
            "/wiki/api/v2/spaces", {"keys": ",".join(self._spaces), "limit": LIMIT}
        )
        found = {str(s.get("key")): s for s in spaces}
        missing = [k for k in self._spaces if k not in found]
        if missing:
            # Never a silent empty listing: a complete scan of nothing would delete everything.
            raise ConnectorError(
                f"Confluence space {', '.join(missing)} not found, or not visible to this account"
            )
        return [found[k] for k in self._spaces]

    async def scan(self, cursor: Mapping[str, Any]) -> ScanResult:
        items: list[RemoteItem] = []
        spaces = await self._resolve_spaces()
        for space in spaces:
            items += await self._space_items(space)
        # Every scan lists everything (versions decide what is fetched): the cursor is only a
        # record of which space ids the keys resolved to.
        return ScanResult(
            items=items,
            complete=True,
            cursor={"spaces": {str(s.get("key")): str(s["id"]) for s in spaces}},
        )

    async def _space_items(self, space: Mapping[str, Any]) -> list[RemoteItem]:
        space_id, key = str(space["id"]), str(space.get("key", ""))
        space_name = str(space.get("name") or key)
        rows = await self._list(
            f"/wiki/api/v2/spaces/{quote(space_id)}/pages", {"limit": LIMIT, "status": "current"}
        )
        pages: dict[str, _Page] = {}
        for row in rows:
            version: dict[str, Any] = row.get("version") or {}
            links: dict[str, Any] = row.get("_links") or {}
            parent = row.get("parentId")
            parent_is_page = row.get("parentType") in (None, "page")
            pages[str(row["id"])] = _Page(
                id=str(row["id"]),
                title=str(row.get("title") or f"Page {row['id']}"),
                parent=str(parent) if parent and parent_is_page else None,
                version=int(version.get("number") or 0),
                created=version.get("createdAt"),
                webui=str(links.get("webui") or ""),
            )
        items: list[RemoteItem] = []
        for page in pages.values():
            ancestors = self._ancestors(page, pages)
            meta = {
                "space_key": key,
                "space_name": space_name,
                "page_id": page.id,
                "parent_id": page.parent,
                "ancestors": [{"id": a.id, "title": a.title} for a in ancestors],
                "path": [space_name, *(a.title for a in ancestors), page.title],
            }
            # The page tree is part of the version: a move or an ancestor's new title refreshes
            # the metadata (the content itself is unchanged, so nothing is re-indexed).
            place = hashlib.sha256(
                json.dumps([key, meta["ancestors"], page.title]).encode()
            ).hexdigest()[:12]
            items.append(
                RemoteItem(
                    external_id=f"page:{page.id}",
                    title=page.title,
                    filename=_filename(page.title, f"page-{page.id}"),
                    version=f"{page.version}:{place}",
                    url=f"{self._site}/wiki{page.webui}" if page.webui else None,
                    modified_at=_when(page.created),
                    meta=meta,
                )
            )
            if self._attachments:
                items += await self._attachment_items(page, meta)
        return items

    @staticmethod
    def _ancestors(page: _Page, pages: Mapping[str, _Page]) -> list[_Page]:
        chain: list[_Page] = []
        seen = {page.id}
        parent = page.parent
        while parent and parent in pages and parent not in seen:
            seen.add(parent)
            chain.append(pages[parent])
            parent = pages[parent].parent
        return list(reversed(chain))

    async def _attachment_items(
        self, page: _Page, page_meta: Mapping[str, Any]
    ) -> list[RemoteItem]:
        rows = await self._list(
            f"/wiki/api/v2/pages/{quote(page.id)}/attachments",
            {"limit": LIMIT, "status": "current"},
        )
        items: list[RemoteItem] = []
        for row in rows:
            name = str(row.get("title") or "")
            if PurePosixPath(name).suffix.lower() not in READABLE:
                continue  # images, archives, ...: nothing WorkMind can read
            version: dict[str, Any] = row.get("version") or {}
            links: dict[str, Any] = row.get("_links") or {}
            webui = row.get("webuiLink") or links.get("webui")
            items.append(
                RemoteItem(
                    external_id=f"attachment:{row['id']}",
                    title=PurePosixPath(name).stem or name,
                    filename=name,
                    version=str(version.get("number") or row.get("fileId") or ""),
                    url=f"{self._site}/wiki{webui}" if isinstance(webui, str) and webui else None,
                    modified_at=_when(version.get("createdAt")),
                    meta={
                        **page_meta,
                        "attachment_id": str(row["id"]),
                        "media_type": row.get("mediaType"),
                    },
                )
            )
        return items

    # --- fetch --------------------------------------------------------------------------------

    async def fetch(self, item: RemoteItem) -> bytes:
        kind, _, item_id = item.external_id.partition(":")
        if kind == "page":
            page = await self._json(
                f"{self._base}/wiki/api/v2/pages/{quote(item_id)}", {"body-format": "storage"}
            )
            body: dict[str, Any] = page.get("body") or {}
            storage: dict[str, Any] = body.get("storage") or {}
            text = storage_to_markdown(str(storage.get("value") or ""))
            title = str(page.get("title") or item.title)
            return f"# {title}\n\n{text}".encode()
        if kind == "attachment":
            return await self._download(item, item_id)
        raise ConnectorError(f"{item.external_id} is not a Confluence item")

    async def _download(self, item: RemoteItem, attachment_id: str) -> bytes:
        page_id = str(item.meta.get("page_id", ""))
        url = (
            f"{self._base}/wiki/rest/api/content/{quote(page_id)}"
            f"/child/attachment/{quote(attachment_id)}/download"
        )
        response = await self._call(url)
        if response.status_code in REDIRECTS:
            # Confluence redirects to its media service with a short-lived token in the URL:
            # follow it without our credentials.
            location = response.headers.get("location", "")
            target = httpx.URL(url).join(location)
            if target.scheme != "https":
                raise ConnectorError(f"{item.title}: Confluence redirected to a non-https URL")
            try:
                check_url(str(target))
            except UnsafeUrlError as exc:
                raise ConnectorError(f"{item.title}: download refused ({exc})") from exc
            response = await send(
                self._http, "GET", str(target), service=SERVICE, sleep=self._sleep
            )
            if response.is_error or response.status_code in REDIRECTS:
                raise ConnectorError(
                    f"could not download {item.title} (HTTP {response.status_code})"
                )
        return response.content
