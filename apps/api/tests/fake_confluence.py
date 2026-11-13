"""A stand-in for Confluence Cloud, shaped after the REST API v2 reference
(https://developer.atlassian.com/cloud/confluence/rest/v2/): spaces, pages in a space (with
version objects, parentId/parentType and `_links.webui`), page bodies in storage format, page
attachments (`webuiLink`, `downloadLink`, version), the v1 attachment download (a redirect to
the media service), cursor pagination through `_links.next` (and a `Link` header), the
api.atlassian.com gateway for OAuth (/ex/confluence/<cloud id>) and the token endpoint.

It cannot prove the real service behaves the same: it is built from the documentation, and
NeedWise has not been run against a live Confluence site (docs/connectors.md).
"""

import base64
import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

SITE = "https://acme.atlassian.net"
CLOUD_ID = "11223344-a1b2-3c4d-5e6f-778899aabbcc"
GATEWAY = f"https://api.atlassian.com/ex/confluence/{CLOUD_ID}"
EMAIL = "sync@acme.test"
API_TOKEN = "ATATT-test-token"
MEDIA = "https://api.media.atlassian.com"


@dataclass
class Page:
    id: str
    space_id: str
    title: str
    body: str
    parent_id: str | None = None
    parent_type: str | None = None
    version: int = 1
    status: str = "current"


@dataclass
class Attachment:
    id: str
    page_id: str
    title: str
    media_type: str
    data: bytes
    version: int = 1
    status: str = "current"


@dataclass
class FakeConfluence:
    page_size: int = 2  # Confluence may return fewer results than `limit`; small pages test paging
    spaces: dict[str, dict[str, Any]] = field(default_factory=dict[str, dict[str, Any]])
    pages: dict[str, Page] = field(default_factory=dict[str, Page])
    attachments: dict[str, Attachment] = field(default_factory=dict[str, Attachment])
    access_token: str = "oauth-access-1"
    refresh_token: str = "oauth-refresh-1"
    refreshes: int = 0
    throttle: int = 0  # answer this many next requests with 429 Retry-After
    requests: list[httpx.Request] = field(default_factory=list[httpx.Request])

    # --- content --------------------------------------------------------------------------

    def space(self, space_id: str, key: str, name: str) -> None:
        self.spaces[space_id] = {
            "id": space_id,
            "key": key,
            "name": name,
            "type": "global",
            "status": "current",
            "authorId": "557058:aaaa",
            "createdAt": "2025-01-10T09:00:00.000Z",
            "homepageId": None,
            "description": None,
            "icon": None,
            "_links": {"webui": f"/spaces/{key}"},
        }

    def page(self, page: Page) -> None:
        self.pages[page.id] = page

    def attach(self, attachment: Attachment) -> None:
        self.attachments[attachment.id] = attachment

    def edit(self, page_id: str, body: str) -> None:
        self.pages[page_id].body = body
        self.pages[page_id].version += 1

    # --- JSON shapes ----------------------------------------------------------------------

    def _space_key(self, space_id: str) -> str:
        return str(self.spaces[space_id]["key"])

    def _version(self, number: int) -> dict[str, Any]:
        return {
            "createdAt": f"2026-10-{number:02d}T10:00:00.000Z",
            "message": "",
            "number": number,
            "minorEdit": False,
            "authorId": "557058:aaaa",
        }

    def _page_json(self, page: Page, body: bool) -> dict[str, Any]:
        key = self._space_key(page.space_id)
        out: dict[str, Any] = {
            "id": page.id,
            "status": page.status,
            "title": page.title,
            "spaceId": page.space_id,
            "parentId": page.parent_id,
            "parentType": page.parent_type,
            "position": 0,
            "authorId": "557058:aaaa",
            "ownerId": "557058:aaaa",
            "lastOwnerId": None,
            "createdAt": "2026-09-01T08:00:00.000Z",
            "version": self._version(page.version),
            "_links": {
                "webui": f"/spaces/{key}/pages/{page.id}/{page.title.replace(' ', '+')}",
                "editui": f"/pages/resumedraft.action?draftId={page.id}",
                "tinyui": f"/x/{page.id[-4:]}",
            },
        }
        if body:
            out["body"] = {"storage": {"representation": "storage", "value": page.body}}
        return out

    def _attachment_json(self, a: Attachment) -> dict[str, Any]:
        preview = f"%2F{a.page_id}%2F{a.id.removeprefix('att')}%2F{a.title}"
        download = f"/download/attachments/{a.page_id}/{a.title}?version={a.version}&api=v2"
        return {
            "id": a.id,
            "status": a.status,
            "title": a.title,
            "createdAt": "2026-09-02T08:00:00.000Z",
            "pageId": a.page_id,
            "mediaType": a.media_type,
            "mediaTypeDescription": "",
            "comment": "",
            "fileId": f"file-{a.id}",
            "fileSize": len(a.data),
            "webuiLink": f"/pages/viewpageattachments.action?pageId={a.page_id}&preview={preview}",
            "downloadLink": download,
            "version": self._version(a.version),
            "_links": {"webui": f"/spaces/x/pages/{a.page_id}", "download": download},
        }

    # --- HTTP -----------------------------------------------------------------------------

    def _list(
        self, request: httpx.Request, path: str, rows: list[dict[str, Any]]
    ) -> httpx.Response:
        query = parse_qs(request.url.query.decode())
        start = int(query.get("cursor", ["0"])[0] or 0)
        size = min(self.page_size, int(query.get("limit", ["25"])[0]))
        chunk = rows[start : start + size]
        links: dict[str, Any] = {"base": f"{SITE}/wiki"}
        headers: dict[str, str] = {}
        if start + size < len(rows):
            params = {k: v[0] for k, v in query.items() if k != "cursor"}
            following = f"{path}?{urlencode({**params, 'cursor': str(start + size)})}"
            links["next"] = following
            headers["Link"] = f'<{following}>; rel="next", <{SITE}/wiki>; rel="base"'
        return httpx.Response(200, json={"results": chunk, "_links": links}, headers=headers)

    def _authorized(self, request: httpx.Request) -> bool:
        header = request.headers.get("authorization", "")
        if request.url.host == "api.atlassian.com":
            return header == f"Bearer {self.access_token}"
        expected = base64.b64encode(f"{EMAIL}:{API_TOKEN}".encode()).decode()
        return header == f"Basic {expected}"

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.throttle:
            self.throttle -= 1
            return httpx.Response(429, headers={"Retry-After": "2"}, text="Rate limited")
        url = request.url
        if url.host == "auth.atlassian.com" and url.path == "/oauth/token":
            return self._token(request)
        if url.host == "api.media.atlassian.com":
            if "authorization" in request.headers:
                return httpx.Response(400, text="credentials must not reach the media service")
            attachment = self.attachments[url.path.split("/")[2].removeprefix("file-")]
            return httpx.Response(200, content=attachment.data)
        if not self._authorized(request):
            return httpx.Response(401, json={"code": 401, "message": "Unauthorized"})
        path = url.path
        if url.host == "api.atlassian.com":
            path = path.removeprefix(f"/ex/confluence/{CLOUD_ID}")
        return self._api(request, path)

    def _token(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("grant_type") != "refresh_token" or body.get("refresh_token") != (
            self.refresh_token
        ):
            return httpx.Response(403, json={"error": "unauthorized_client"})
        self.refreshes += 1
        self.access_token = f"oauth-access-{self.refreshes + 1}"
        self.refresh_token = f"oauth-refresh-{self.refreshes + 1}"  # rotating refresh tokens
        return httpx.Response(
            200,
            json={
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "expires_in": 3600,
                "scope": "read:page:confluence offline_access",
                "token_type": "Bearer",
            },
        )

    def _api(self, request: httpx.Request, path: str) -> httpx.Response:
        query = parse_qs(request.url.query.decode())
        parts = urlsplit(path).path.strip("/").split("/")
        if path == "/wiki/api/v2/spaces":
            keys = query.get("keys", [""])[0].split(",")
            rows = [s for s in self.spaces.values() if s["key"] in keys]
            return self._list(request, path, rows)
        if parts[:4] == ["wiki", "api", "v2", "spaces"] and parts[-1] == "pages":
            status = query.get("status", ["current,archived"])[0].split(",")
            rows = [
                self._page_json(p, body=False)
                for p in self.pages.values()
                if p.space_id == parts[4] and p.status in status
            ]
            return self._list(request, path, rows)
        if parts[:4] == ["wiki", "api", "v2", "pages"] and len(parts) == 5:
            page = self.pages.get(parts[4])
            if page is None or page.status != "current":
                return httpx.Response(404, json={"errors": [{"status": 404, "code": "NOT_FOUND"}]})
            return httpx.Response(200, json=self._page_json(page, body="body-format" in query))
        if parts[:4] == ["wiki", "api", "v2", "pages"] and parts[-1] == "attachments":
            rows = [
                self._attachment_json(a)
                for a in self.attachments.values()
                if a.page_id == parts[4] and a.status == "current"
            ]
            return self._list(request, path, rows)
        if parts[:4] == ["wiki", "rest", "api", "content"] and parts[-1] == "download":
            attachment = self.attachments.get(parts[-2])
            if attachment is None:
                return httpx.Response(404)
            location = (
                f"{MEDIA}/file/file-{attachment.id}/binary?token=media-jwt&client=c1&dl=true"
                f"&name={attachment.title}"
            )
            return httpx.Response(302, headers={"Location": location})
        return httpx.Response(404, json={"errors": [{"status": 404, "code": "NOT_FOUND"}]})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle))
