"""Google Drive: the files of one folder and its subfolders (Drive API v3, OAuth with the
drive.readonly scope). Google Docs, Sheets and Slides are exported as DOCX, XLSX and PPTX."""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import PurePath
from typing import Any

import httpx

from workmind.modules.connectors.base import ConnectorError, RemoteItem, ScanResult

API = "https://www.googleapis.com/drive/v3"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105  (an endpoint, not a secret)
FOLDER = "application/vnd.google-apps.folder"
EXPORTS = {
    "application/vnd.google-apps.document": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".docx",
    ),
    "application/vnd.google-apps.spreadsheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsx",
    ),
    "application/vnd.google-apps.presentation": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".pptx",
    ),
}
READABLE = {".pdf", ".docx", ".xlsx", ".pptx", ".md", ".txt", ".html", ".csv"}
FIELDS = "nextPageToken, files(id, name, mimeType, modifiedTime, version, webViewLink, size)"


class GoogleDriveConnector:
    kind = "gdrive"

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        folder_id: str,
        credentials: Mapping[str, Any],
        client_id: str | None,
        client_secret: str | None,
    ) -> None:
        if not credentials.get("refresh_token") and not credentials.get("access_token"):
            raise ConnectorError("connect Google Drive first (OAuth)")
        self._http = http
        self._folder = folder_id
        self._credentials = dict(credentials)
        self._client = (client_id, client_secret)
        self._cache: dict[str, tuple[str, str | None]] = {}  # file id -> (mime, export mime)
        # Set when the access token was refreshed: the sync engine stores it, encrypted.
        self.updated_credentials: dict[str, Any] | None = None

    async def _token(self) -> str:
        expires = self._credentials.get("expires_at")
        fresh = expires and datetime.fromisoformat(expires) > datetime.now(UTC) + timedelta(
            minutes=2
        )
        if self._credentials.get("access_token") and fresh:
            return str(self._credentials["access_token"])
        client_id, client_secret = self._client
        if not (client_id and client_secret and self._credentials.get("refresh_token")):
            raise ConnectorError("the Google access token expired; reconnect Google Drive")
        try:
            response = await self._http.post(
                TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self._credentials["refresh_token"],
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
                timeout=20,
            )
        except httpx.HTTPError as exc:
            raise ConnectorError(f"Google is unreachable: {exc}") from exc
        if response.is_error:
            raise ConnectorError("Google refused the refresh token; reconnect Google Drive")
        body = response.json()
        self._credentials["access_token"] = body["access_token"]
        self._credentials["expires_at"] = (
            datetime.now(UTC) + timedelta(seconds=int(body.get("expires_in", 3600)))
        ).isoformat()
        self.updated_credentials = dict(self._credentials)
        return str(body["access_token"])

    async def _get(self, path: str, **params: str) -> httpx.Response:
        headers = {"Authorization": f"Bearer {await self._token()}"}
        try:
            response = await self._http.get(
                f"{API}{path}", params=params, headers=headers, timeout=60
            )
        except httpx.HTTPError as exc:
            raise ConnectorError(f"Google Drive is unreachable: {exc}") from exc
        if response.status_code in (401, 403):
            raise ConnectorError("Google Drive refused access; check the folder is shared")
        if response.status_code == 429:
            raise ConnectorError("Google Drive rate limit reached")
        if response.is_error:
            raise ConnectorError(f"Google Drive answered HTTP {response.status_code}")
        return response

    async def scan(self, cursor: Mapping[str, Any]) -> ScanResult:
        items: list[RemoteItem] = []
        folders = [self._folder]
        while folders:
            folder = folders.pop()
            page_token = ""
            while True:
                params = {
                    "q": f"'{folder}' in parents and trashed = false",
                    "fields": FIELDS,
                    "pageSize": "200",
                    "supportsAllDrives": "true",
                    "includeItemsFromAllDrives": "true",
                }
                if page_token:
                    params["pageToken"] = page_token
                page = (await self._get("/files", **params)).json()
                for f in page.get("files", []):
                    item = self._item(f)
                    if f["mimeType"] == FOLDER:
                        folders.append(f["id"])
                    elif item is not None:
                        items.append(item)
                page_token = page.get("nextPageToken", "")
                if not page_token:
                    break
        return ScanResult(items=items, complete=True)

    def _item(self, f: Mapping[str, Any]) -> RemoteItem | None:
        mime = f["mimeType"]
        if mime in EXPORTS:
            export, extension = EXPORTS[mime]
            filename = PurePath(f["name"]).stem + extension
        else:
            export, filename = None, f["name"]
            if PurePath(filename).suffix.lower() not in READABLE:
                return None
        self._cache[f["id"]] = (mime, export)
        return RemoteItem(
            external_id=f["id"],
            title=PurePath(f["name"]).stem,
            filename=filename,
            version=str(f.get("version") or f.get("modifiedTime")),
            url=f.get("webViewLink"),
        )

    async def fetch(self, item: RemoteItem) -> bytes:
        _, export = self._cache.get(item.external_id, ("", None))
        if export:
            return (await self._get(f"/files/{item.external_id}/export", mimeType=export)).content
        return (await self._get(f"/files/{item.external_id}", alt="media")).content
