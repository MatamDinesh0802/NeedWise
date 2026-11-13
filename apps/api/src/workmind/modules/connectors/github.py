"""GitHub: documentation files of one repository (REST API; a token is optional for public
repositories and needs only read access to contents)."""

from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any

import httpx

from workmind.modules.connectors.base import (
    ConnectorError,
    ConnectorRefusedError,
    RemoteItem,
    ScanResult,
)

API = "https://api.github.com"
# Extensions WorkMind parses; others are mapped onto a parser that reads them correctly.
READABLE = {
    ".md": ".md",
    ".mdx": ".md",
    ".markdown": ".md",
    ".txt": ".txt",
    ".rst": ".txt",
    ".pdf": ".pdf",
}


class GitHubConnector:
    kind = "github"

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        repo: str,
        branch: str | None = None,
        paths: list[str] | None = None,
        token: str | None = None,
    ) -> None:
        if repo.count("/") != 1:
            raise ConnectorError("repo must look like owner/name")
        self._http = http
        self._repo = repo
        self._branch = branch
        self._paths = [p.strip("/") for p in (paths or [""])]
        self._headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            self._headers["Authorization"] = f"Bearer {token}"

    async def _get(self, path: str, **params: str) -> httpx.Response:
        try:
            response = await self._http.get(
                f"{API}{path}", params=params, headers=self._headers, timeout=30
            )
        except httpx.HTTPError as exc:
            raise ConnectorError(f"GitHub is unreachable: {exc}") from exc
        if response.status_code == 403 and response.headers.get("x-ratelimit-remaining") == "0":
            raise ConnectorError("GitHub rate limit reached; add a token or wait")
        if response.status_code in (401, 403):
            raise ConnectorRefusedError("GitHub refused the credentials")
        if response.status_code == 404:
            raise ConnectorRefusedError(f"{self._repo} not found (or the token cannot see it)")
        if response.is_error:
            raise ConnectorError(f"GitHub answered HTTP {response.status_code}")
        return response

    @property
    def repo(self) -> str:
        return self._repo

    async def get(self, path: str, **params: str) -> httpx.Response:
        """A read-only REST call with this source's token (permission sync uses it)."""
        return await self._get(path, **params)

    def _wanted(self, path: str) -> bool:
        in_paths = any(p == "" or path == p or path.startswith(p + "/") for p in self._paths)
        return in_paths and PurePosixPath(path).suffix.lower() in READABLE

    async def scan(self, cursor: Mapping[str, Any]) -> ScanResult:
        branch = self._branch or (await self._get(f"/repos/{self._repo}")).json()["default_branch"]
        self._branch = branch
        tree = (await self._get(f"/repos/{self._repo}/git/trees/{branch}", recursive="1")).json()
        items = [
            RemoteItem(
                external_id=entry["path"],
                title=PurePosixPath(entry["path"]).stem.replace("-", " ").replace("_", " ").strip()
                or entry["path"],
                filename=PurePosixPath(entry["path"]).stem
                + READABLE[PurePosixPath(entry["path"]).suffix.lower()],
                version=entry["sha"],  # the blob's content hash: changes exactly with the file
                url=f"https://github.com/{self._repo}/blob/{branch}/{entry['path']}",
                meta={"path": entry["path"]},
            )
            for entry in tree.get("tree", [])
            if entry.get("type") == "blob" and self._wanted(entry["path"])
        ]
        # GitHub truncates very large trees: then some files were not listed, not deleted.
        return ScanResult(
            items=items, complete=not tree.get("truncated"), cursor={"branch": branch}
        )

    async def fetch(self, item: RemoteItem) -> bytes:
        try:
            response = await self._http.get(
                f"{API}/repos/{self._repo}/contents/{item.external_id}",
                params={"ref": self._branch or ""},
                headers={**self._headers, "Accept": "application/vnd.github.raw"},
                timeout=30,
            )
        except httpx.HTTPError as exc:
            raise ConnectorError(f"GitHub is unreachable: {exc}") from exc
        if response.is_error:
            raise ConnectorError(
                f"could not download {item.external_id} (HTTP {response.status_code})"
            )
        return response.content
