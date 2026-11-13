"""A stand-in for Microsoft Graph (v1.0) and the Microsoft identity platform token endpoint,
shaped after their documentation:

- sites by path (GET /sites/{hostname}:/{path}), a site's drives and default drive, /me/drive,
  an item by path (GET /drives/{id}/root:/{path});
- driveItem delta (https://learn.microsoft.com/graph/api/driveitem-delta): the first call
  enumerates the whole drive, pages carry `@odata.nextLink` and the last one an
  `@odata.deltaLink`; later calls return the latest state of each changed item, deleted items
  as tombstones (`deleted` facet, minimal properties), without paths in `parentReference`;
  moving or deleting a folder reports only the folder, not what is inside it (the documented
  worst case); an expired token answers 410 Gone (resyncRequired);
- downloads: GET /drives/{id}/items/{id}/content answers 302 with a pre-authenticated URL;
- throttling: 429 with Retry-After (https://learn.microsoft.com/graph/throttling);
- refresh tokens that rotate on every renewal.

It cannot prove the real service behaves the same: WorkMind has not been run against a live
Microsoft 365 tenant (docs/connectors.md).
"""

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, quote, unquote

import httpx

GRAPH = "https://graph.microsoft.com/v1.0"
HOST = "contoso.sharepoint.com"
SITE_URL = f"https://{HOST}/sites/finance"
SITE_ID = f"{HOST},2C712604-1370-44E7-A1F5-426573FDA80A,2D2244C3-251A-49EA-93A8-39E1C3A060FE"
DRIVE_ID = "b!t18F8ybsHUq1z3LTz8xvZqP8zaSWjkFNhsME-Fepo75dTf9vQKfeRblBZjoSQrd7"
OTHER_DRIVE_ID = "b!t18F8ybsHUq1z3LTz8xvZqP8zaSWjkFNhsME-Fepo75dTf9vQKfeRblBZjoSQrd8"
ROOT_ID = "01BYE5RZ56Y2GOVW7725BZO354PWSELRRZ"


@dataclass
class Item:
    id: str
    name: str
    parent: str | None
    folder: bool = False
    data: bytes = b""
    version: int = 1
    deleted: bool = False


@dataclass
class FakeGraph:
    page_size: int = 3
    items: dict[str, Item] = field(default_factory=dict[str, Item])
    changes: list[str] = field(default_factory=list[str])  # item ids, in the order they changed
    expired_before: int = 0  # delta tokens older than this change number answer 410
    access_token: str = "graph-access-1"
    refresh_token: str = "graph-refresh-1"
    refreshes: int = 0
    throttle: int = 0  # answer this many next requests with 429
    retry_after: str = "1"
    requests: list[httpx.Request] = field(default_factory=list[httpx.Request])

    def __post_init__(self) -> None:
        self.items[ROOT_ID] = Item(ROOT_ID, "root", None, folder=True)

    # --- content --------------------------------------------------------------------------

    def put(self, item: Item) -> Item:
        existing = self.items.get(item.id)
        if existing is not None:
            item.version = existing.version + 1
        self.items[item.id] = item
        self.changes.append(item.id)
        return item

    def write(self, item_id: str, data: bytes) -> None:
        item = self.items[item_id]
        item.data, item.version = data, item.version + 1
        self.changes.append(item_id)

    def move(self, item_id: str, parent: str) -> None:
        self.items[item_id].parent = parent
        self.changes.append(item_id)  # only the moved item itself, never its descendants

    def delete(self, item_id: str) -> None:
        self.items[item_id].deleted = True
        self.changes.append(item_id)

    # --- JSON shapes ----------------------------------------------------------------------

    def _path(self, item: Item) -> str:
        names: list[str] = []
        current: Item | None = item
        while current is not None and current.parent is not None:
            names.append(current.name)
            current = self.items.get(current.parent)
        return "/".join(reversed(names))

    def _json(self, item: Item) -> dict[str, Any]:
        if item.deleted:  # a tombstone carries little more than its id (OneDrive for Business)
            return {
                "id": item.id,
                "deleted": {"state": "deleted"},
                "parentReference": {"driveId": DRIVE_ID, "driveType": "documentLibrary"},
            }
        out: dict[str, Any] = {
            "id": item.id,
            "name": item.name,
            "eTag": f'"{{{item.id[-8:]}}},{item.version}"',
            "lastModifiedDateTime": f"2026-10-{min(item.version, 28):02d}T10:00:00Z",
            "webUrl": f"{SITE_URL}/Shared%20Documents/{quote(self._path(item))}",
            # Delta responses leave out the parent's path (renames would make it stale).
            "parentReference": {"driveId": DRIVE_ID, "driveType": "documentLibrary"},
        }
        if item.parent:
            out["parentReference"]["id"] = item.parent
        if item.id == ROOT_ID:
            out["root"] = {}
        if item.folder:
            out["folder"] = {
                "childCount": sum(1 for i in self.items.values() if i.parent == item.id)
            }
        else:
            out["cTag"] = f'"c:{{{item.id[-8:]}}},{item.version}"'
            out["size"] = len(item.data)
            out["file"] = {"mimeType": "application/octet-stream", "hashes": {}}
        return out

    # --- HTTP -----------------------------------------------------------------------------

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.throttle:
            self.throttle -= 1
            return httpx.Response(
                429,
                headers={"Retry-After": self.retry_after},
                json={"error": {"code": "TooManyRequests", "message": "Please retry again later."}},
            )
        url = request.url
        if url.host == "login.microsoftonline.com":
            return self._token(request)
        if url.host == HOST and url.path.endswith("/download.aspx"):
            if "authorization" in request.headers:
                return httpx.Response(400, text="the download URL is pre-authenticated")
            item = self.items[parse_qs(url.query.decode())["UniqueId"][0]]
            return httpx.Response(200, content=item.data)
        if request.headers.get("authorization") != f"Bearer {self.access_token}":
            return httpx.Response(
                401, json={"error": {"code": "InvalidAuthenticationToken", "message": "expired"}}
            )
        return self._api(request)

    def _token(self, request: httpx.Request) -> httpx.Response:
        form = parse_qs(request.content.decode())
        if form.get("grant_type") != ["refresh_token"] or form.get("refresh_token") != [
            self.refresh_token
        ]:
            return httpx.Response(400, json={"error": "invalid_grant"})
        self.refreshes += 1
        self.access_token = f"graph-access-{self.refreshes + 1}"
        self.refresh_token = f"graph-refresh-{self.refreshes + 1}"
        return httpx.Response(
            200,
            json={
                "token_type": "Bearer",
                "scope": form.get("scope", [""])[0],
                "expires_in": 3599,
                "ext_expires_in": 3599,
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
            },
        )

    def _not_found(self) -> httpx.Response:
        return httpx.Response(404, json={"error": {"code": "itemNotFound", "message": "nope"}})

    def _api(self, request: httpx.Request) -> httpx.Response:
        path = unquote(request.url.path.removeprefix("/v1.0"))
        if path == f"/sites/{HOST}:/sites/finance":
            return httpx.Response(
                200,
                json={
                    "id": SITE_ID,
                    "name": "finance",
                    "displayName": "Finance",
                    "webUrl": SITE_URL,
                },
            )
        if path == f"/sites/{SITE_ID}/drives":
            return httpx.Response(
                200,
                json={
                    "value": [
                        {"id": DRIVE_ID, "name": "Documents", "driveType": "documentLibrary"},
                        {"id": OTHER_DRIVE_ID, "name": "Archive", "driveType": "documentLibrary"},
                    ]
                },
            )
        if path in (f"/sites/{SITE_ID}/drive", "/me/drive", f"/drives/{DRIVE_ID}"):
            return httpx.Response(200, json={"id": DRIVE_ID, "name": "Documents"})
        prefix = f"/drives/{DRIVE_ID}/root:/"
        if path.startswith(prefix):
            wanted = path.removeprefix(prefix).strip("/")
            for item in self.items.values():
                if not item.deleted and self._path(item) == wanted:
                    return httpx.Response(200, json=self._json(item))
            return self._not_found()
        if path == f"/drives/{DRIVE_ID}/root/delta":
            return self._delta(request)
        if path.startswith(f"/drives/{DRIVE_ID}/items/") and path.endswith("/content"):
            item = self.items.get(path.split("/")[4])
            if item is None or item.deleted:
                return self._not_found()
            location = f"https://{HOST}/sites/finance/_layouts/15/download.aspx?UniqueId={item.id}&tempauth=x"
            return httpx.Response(302, headers={"Location": location})
        return self._not_found()

    def _delta(self, request: httpx.Request) -> httpx.Response:
        query = parse_qs(request.url.query.decode())
        token = query.get("token", [""])[0]
        start = int(query.get("skip", ["0"])[0])
        if token.startswith("state-"):
            since = int(token.removeprefix("state-"))
            if since < self.expired_before:
                return httpx.Response(
                    410,
                    json={"error": {"code": "resyncRequired", "message": "Resync required."}},
                )
            changed = list(dict.fromkeys(reversed(self.changes[since:])))[::-1]
            rows = [self._json(self.items[i]) for i in changed]
        else:  # the first round: everything there is now
            rows = [self._json(i) for i in self.items.values() if not i.deleted]
        chunk = rows[start : start + self.page_size]
        body: dict[str, Any] = {
            "@odata.context": f"{GRAPH}/$metadata#Collection(driveItem)",
            "value": chunk,
        }
        base = f"{GRAPH}/drives/{DRIVE_ID}/root/delta"
        if start + self.page_size < len(rows):
            body["@odata.nextLink"] = (
                f"{base}?token={token or 'first'}&skip={start + self.page_size}"
            )
        else:
            body["@odata.deltaLink"] = f"{base}?token=state-{len(self.changes)}"
        return httpx.Response(200, json=body)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle))
