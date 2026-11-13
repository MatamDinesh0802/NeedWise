"""SharePoint and OneDrive: the files of one document library (a drive), or of one folder in
it, through Microsoft Graph (https://learn.microsoft.com/graph/api/driveitem-delta).

Which drive:
- `site_url` set: that SharePoint site's library named `library` (or its default library);
- no `site_url` but `library`: the drive with that id;
- neither: the OneDrive of the account that connected.

Sync is incremental with delta queries. The first scan enumerates the whole drive (a complete
scan: files no longer there are deleted); later scans ask only for changes since the stored
`@odata.deltaLink`, and a deleted file arrives as a tombstone (the `deleted` facet) that
removes it from WorkMind. Graph's delta works on a drive's root only, and on SharePoint it
reports items by id without paths, so the connector keeps the drive's folder tree (ids,
parents, names) in the source's cursor to tell which files are inside the selected folder.
When a folder inside the selection is deleted, or a folder moves into or out of it, Graph does
not report the files underneath one by one: the connector then enumerates the drive again
(complete), which settles both. An expired delta token (HTTP 410) does the same.

Throttling (429, or 503 with Retry-After) is waited out as Graph asks; see transport.py.
"""

import asyncio
from collections.abc import Mapping
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from workmind.core.urls import UnsafeUrlError, check_url
from workmind.modules.connectors.base import ConnectorError, RemoteItem, ScanResult
from workmind.modules.connectors.oauth import (
    OAuthApp,
    RefreshingToken,
    microsoft_scopes,
    microsoft_token_url,
)
from workmind.modules.connectors.transport import REDIRECTS, Sleep, send

GRAPH = "https://graph.microsoft.com/v1.0"
SERVICE = "Microsoft Graph"
# Properties a delta page carries per item (Graph returns the rest only on request).
SELECT = (
    "id,name,eTag,cTag,file,folder,root,deleted,parentReference,webUrl,lastModifiedDateTime,size"
)
READABLE = {".pdf", ".docx", ".xlsx", ".pptx", ".md", ".txt", ".html", ".htm", ".csv"}
MAX_PAGES = 20_000  # delta pages per scan: a guard against a link that never ends
MAX_DEPTH = 500  # folder nesting, against a cycle in a damaged tree

Folders = dict[str, list[str | None]]  # folder id -> [parent id, name]


class _ResyncRequiredError(Exception):
    """Graph answered 410 Gone: the delta token is no longer valid."""


def _graph_link(url: object) -> str | None:
    """Only Graph's own links are followed (a stored or returned link carries our token)."""
    if isinstance(url, str) and url.startswith(f"{GRAPH}/"):
        return url
    return None


def _when(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_folder(entry: Mapping[str, Any]) -> bool:
    return "folder" in entry or "root" in entry


def _parent(entry: Mapping[str, Any]) -> str | None:
    reference: dict[str, Any] = entry.get("parentReference") or {}
    parent = reference.get("id")
    return str(parent) if parent else None


class SharePointConnector:
    kind = "sharepoint"

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        site_url: str | None,
        library: str | None,
        folder: str | None,
        credentials: Mapping[str, Any],
        app: OAuthApp,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if not credentials.get("refresh_token") and not credentials.get("access_token"):
            raise ConnectorError("connect Microsoft 365 first (OAuth)")
        self._http = http
        self._site = site_url.rstrip("/") if site_url else None
        self._library = library
        self._folder = folder.strip("/") if folder else None
        self._sleep = sleep
        scope = credentials.get("scope") or " ".join(
            microsoft_scopes({"site_url": site_url, "library": library})
        )
        self._token = RefreshingToken(
            http,
            credentials,
            token_url=microsoft_token_url(app.tenant),
            app=app,
            service="Microsoft",
            reconnect="reconnect Microsoft 365",
            as_json=False,
            extra={"scope": str(scope)},
            sleep=sleep,
        )
        self._drive_id: str | None = None

    @property
    def updated_credentials(self) -> dict[str, Any] | None:
        return self._token.updated

    # --- HTTP ---------------------------------------------------------------------------------

    async def _send(self, url: str, params: Mapping[str, str] | None) -> httpx.Response:
        return await send(
            self._http,
            "GET",
            url,
            service=SERVICE,
            sleep=self._sleep,
            headers={"Authorization": f"Bearer {await self._token.get()}"},
            params=params,
        )

    async def _call(self, url: str, params: Mapping[str, str] | None = None) -> httpx.Response:
        response = await self._send(url, params)
        if response.status_code == 401:
            self._token.expire()  # revoked or expired early: renew once
            response = await self._send(url, params)
        if response.status_code == 401:
            raise ConnectorError("Microsoft Graph refused the token; reconnect Microsoft 365")
        if response.status_code == 403:
            raise ConnectorError(
                "Microsoft Graph denied access; check the account can read this site or library"
            )
        if response.status_code == 410:
            raise _ResyncRequiredError
        return response

    async def _json(
        self, url: str, params: Mapping[str, str] | None = None, *, what: str = ""
    ) -> dict[str, Any]:
        response = await self._call(url, params)
        if response.status_code == 404:
            raise ConnectorError(f"Microsoft Graph: {what or 'item'} not found")
        if response.is_error:
            raise ConnectorError(f"Microsoft Graph answered HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError as exc:
            raise ConnectorError("Microsoft Graph sent a response that is not JSON") from exc
        if not isinstance(body, dict):
            raise ConnectorError("Microsoft Graph sent an unexpected response")
        return body  # pyright: ignore[reportUnknownVariableType]

    # --- which drive and folder ---------------------------------------------------------------

    async def _resolve(self) -> tuple[str, str | None]:
        """The drive id, and the selected folder's id (None: the whole drive)."""
        if self._site:
            parts = urlsplit(self._site)
            path = quote(parts.path.rstrip("/"))
            site = await self._json(
                f"{GRAPH}/sites/{parts.hostname}:{path}"
                if path
                else f"{GRAPH}/sites/{parts.hostname}",
                what=f"site {self._site}",
            )
            site_id = quote(str(site["id"]), safe="")
            if self._library:
                drive = await self._library_drive(site_id)
            else:
                drive = await self._json(f"{GRAPH}/sites/{site_id}/drive", what="default library")
        elif self._library:
            drive = await self._json(
                f"{GRAPH}/drives/{quote(self._library, safe='')}", what=f"drive {self._library}"
            )
        else:
            drive = await self._json(f"{GRAPH}/me/drive", what="OneDrive")
        drive_id = str(drive["id"])
        self._drive_id = drive_id
        if not self._folder:
            return drive_id, None
        folder = await self._json(
            f"{GRAPH}/drives/{quote(drive_id, safe='')}/root:/{quote(self._folder)}",
            what=f"folder {self._folder}",
        )
        if "folder" not in folder:
            raise ConnectorError(f"{self._folder} is a file, not a folder")
        return drive_id, str(folder["id"])

    async def _library_drive(self, site_id: str) -> dict[str, Any]:
        wanted = (self._library or "").casefold()
        url: str | None = f"{GRAPH}/sites/{site_id}/drives"
        for _ in range(MAX_PAGES):
            if url is None:
                break
            page = await self._json(url, what="document libraries")
            for drive in page.get("value", []):
                if str(drive.get("id")) == self._library or (
                    str(drive.get("name", "")).casefold() == wanted
                ):
                    return drive
            url = _graph_link(page.get("@odata.nextLink"))
        raise ConnectorError(f"no document library named {self._library!r} on {self._site}")

    # --- scan ---------------------------------------------------------------------------------

    async def _delta(self, url: str, params: Mapping[str, str] | None) -> tuple[list[Any], str]:
        """Every change from `url` to the end of the feed, and the link for next time."""
        entries: list[Any] = []
        for _ in range(MAX_PAGES):
            page = await self._json(url, params, what="drive")
            values: list[Any] = page.get("value") or []
            entries += [e for e in values if isinstance(e, dict) and "id" in e]
            following = _graph_link(page.get("@odata.nextLink"))
            if following:
                url, params = following, None
                continue
            delta = _graph_link(page.get("@odata.deltaLink"))
            if delta is None:
                raise ConnectorError("Microsoft Graph ended a delta feed without a delta link")
            return entries, delta
        raise ConnectorError("Microsoft Graph kept paging; the sync was stopped")

    async def scan(self, cursor: Mapping[str, Any]) -> ScanResult:
        drive_id, scope = await self._resolve()
        same = cursor.get("drive_id") == drive_id and cursor.get("scope") == (scope or "")
        link = _graph_link(cursor.get("delta_link")) if same else None
        if link:
            folders: Folders = {
                str(k): list(v) for k, v in dict(cursor.get("folders") or {}).items()
            }
            try:
                entries, delta = await self._delta(link, None)
            except _ResyncRequiredError:
                entries, delta = [], ""
            else:
                changes = self._changes(_latest(entries), folders, scope)
                if changes is not None:
                    items, deleted = changes
                    return ScanResult(
                        items=items,
                        complete=False,
                        cursor=self._cursor(drive_id, scope, delta, folders),
                        deleted=deleted,
                    )
        # First sync, another drive or folder, an expired token, or a folder that entered or
        # left the selection: enumerate everything.
        entries, delta = await self._delta(
            f"{GRAPH}/drives/{quote(drive_id, safe='')}/root/delta", {"$select": SELECT}
        )
        folders = {}
        latest = _latest(entries)
        for entry in latest:
            if _is_folder(entry) and not entry.get("deleted"):
                folders[str(entry["id"])] = [_parent(entry), str(entry.get("name", ""))]
        items = [
            item
            for entry in latest
            if not entry.get("deleted") and not _is_folder(entry)
            if (item := self._item(entry, folders, scope)) is not None
        ]
        return ScanResult(
            items=items, complete=True, cursor=self._cursor(drive_id, scope, delta, folders)
        )

    @staticmethod
    def _cursor(drive_id: str, scope: str | None, delta: str, folders: Folders) -> dict[str, Any]:
        return {"drive_id": drive_id, "scope": scope or "", "delta_link": delta, "folders": folders}

    def _changes(
        self, entries: list[dict[str, Any]], folders: Folders, scope: str | None
    ) -> tuple[list[RemoteItem], list[str]] | None:
        """Applies a delta page set to the folder tree and turns file changes into items and
        deletions. None: the selection changed shape, so a full enumeration is needed."""
        before: Folders = dict(folders)
        moved: list[str] = []
        for entry in entries:
            entry_id = str(entry["id"])
            if entry.get("deleted"):
                if entry_id in folders:
                    if _within(before, entry_id, scope):
                        return None  # its files may not be reported one by one
                    folders.pop(entry_id)
                continue
            if _is_folder(entry):
                if entry_id in before:
                    moved.append(entry_id)
                folders[entry_id] = [_parent(entry), str(entry.get("name", ""))]
        if any(_within(before, f, scope) != _within(folders, f, scope) for f in moved):
            return None
        items: list[RemoteItem] = []
        deleted: list[str] = []
        for entry in entries:
            entry_id = str(entry["id"])
            if _is_folder(entry) or entry_id in before:
                continue
            item = None if entry.get("deleted") else self._item(entry, folders, scope)
            if item is None:
                # Deleted, moved out of the folder, or renamed to a type WorkMind cannot read:
                # gone from the selection either way (an id WorkMind never synced is ignored).
                deleted.append(entry_id)
            else:
                items.append(item)
        return items, deleted

    def _item(
        self, entry: Mapping[str, Any], folders: Folders, scope: str | None
    ) -> RemoteItem | None:
        if "file" not in entry:
            return None  # a OneNote notebook, a package, ...
        parent = _parent(entry)
        if scope is not None and not _within(folders, parent, scope):
            return None
        name = str(entry.get("name", ""))
        if PurePosixPath(name).suffix.lower() not in READABLE:
            return None
        # cTag changes with the content only (eTag also with a rename or a move).
        version = entry.get("cTag") or entry.get("eTag") or entry.get("lastModifiedDateTime")
        if not version:
            return None
        return RemoteItem(
            external_id=str(entry["id"]),
            title=PurePosixPath(name).stem or name,
            filename=name,
            version=str(version),
            url=entry.get("webUrl"),
            modified_at=_when(entry.get("lastModifiedDateTime")),
            meta={
                "drive_id": self._drive_id,
                "site_url": self._site,
                "path": _path(folders, parent),
                "size": entry.get("size"),
            },
        )

    # --- fetch --------------------------------------------------------------------------------

    async def fetch(self, item: RemoteItem) -> bytes:
        drive_id = str(item.meta.get("drive_id") or self._drive_id or "")
        if not drive_id:
            raise ConnectorError(f"{item.title}: unknown drive")
        url = (
            f"{GRAPH}/drives/{quote(drive_id, safe='')}"
            f"/items/{quote(item.external_id, safe='')}/content"
        )
        try:
            response = await self._call(url)
        except _ResyncRequiredError as exc:
            raise ConnectorError(f"{item.title}: no longer available (HTTP 410)") from exc
        if response.status_code in REDIRECTS:
            # A pre-authenticated download URL (valid for a few minutes): no token goes there.
            target = httpx.URL(url).join(response.headers.get("location", ""))
            if target.scheme != "https":
                raise ConnectorError(f"{item.title}: Graph redirected to a non-https URL")
            try:
                check_url(str(target))
            except UnsafeUrlError as exc:
                raise ConnectorError(f"{item.title}: download refused ({exc})") from exc
            response = await send(
                self._http, "GET", str(target), service=SERVICE, sleep=self._sleep
            )
        if response.status_code == 404:
            raise ConnectorError(f"{item.title}: not found (deleted since the scan?)")
        if response.is_error or response.status_code in REDIRECTS:
            raise ConnectorError(f"could not download {item.title} (HTTP {response.status_code})")
        return response.content


def _latest(entries: list[Any]) -> list[dict[str, Any]]:
    """An item can appear more than once in a delta feed: its last occurrence wins (Graph)."""
    latest: dict[str, dict[str, Any]] = {}
    for entry in entries:
        latest.pop(str(entry["id"]), None)
        latest[str(entry["id"])] = entry
    return list(latest.values())


def _within(folders: Folders, folder_id: str | None, scope: str | None) -> bool:
    """Whether a folder is the selected folder or inside it (anything is, without one)."""
    if scope is None:
        return True
    current = folder_id
    for _ in range(MAX_DEPTH):
        if current is None:
            return False
        if current == scope:
            return True
        entry = folders.get(current)
        if entry is None:
            return False
        current = entry[0]
    return False


def _path(folders: Folders, folder_id: str | None) -> str:
    names: list[str] = []
    current = folder_id
    for _ in range(MAX_DEPTH):
        entry = folders.get(current) if current else None
        if entry is None or entry[0] is None:  # the root has no parent and no useful name
            break
        names.append(str(entry[1]))
        current = entry[0]
    return "/".join(reversed(names))
