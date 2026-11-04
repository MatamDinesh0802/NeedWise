"""Web crawler: pages under a start URL, on the same host, honouring robots.txt."""

import hashlib
import re
from collections import deque
from collections.abc import Mapping
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urldefrag, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from workmind.modules.connectors.base import ConnectorError, RemoteItem, ScanResult

USER_AGENT = "WorkMindBot/0.6 (+https://github.com/workmind-ai)"
EXTENSIONS = {
    "text/html": ".html",
    "application/pdf": ".pdf",
    "text/markdown": ".md",
    "text/plain": ".txt",
}


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)
        self._in_title = tag == "title"

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data


def _filename(url: str, extension: str) -> str:
    path = urlsplit(url).path.strip("/") or "index"
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", path).strip("-")[:80] or "page"
    return slug + extension


class WebConnector:
    kind = "web"

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        start_urls: list[str],
        max_pages: int = 100,
        path_prefix: str | None = None,
    ) -> None:
        if not start_urls:
            raise ConnectorError("a web source needs at least one start URL")
        self._http = http
        self._start = [urldefrag(u)[0] for u in start_urls]
        self._max = max_pages
        first = urlsplit(self._start[0])
        self._host = first.netloc
        self._prefix = path_prefix if path_prefix is not None else first.path.rsplit("/", 1)[0]
        self._cache: dict[str, bytes] = {}
        self._robots: RobotFileParser | None = None

    async def _allowed(self, url: str) -> bool:
        if self._robots is None:
            robots = RobotFileParser()
            base = urlsplit(self._start[0])
            try:
                response = await self._http.get(
                    f"{base.scheme}://{base.netloc}/robots.txt", timeout=10
                )
                robots.parse(response.text.splitlines() if response.status_code == 200 else [])
            except httpx.HTTPError:
                robots.parse([])
            self._robots = robots
        return self._robots.can_fetch(USER_AGENT, url)

    def _in_scope(self, url: str) -> bool:
        parts = urlsplit(url)
        return (
            parts.scheme in ("http", "https")
            and parts.netloc == self._host
            and parts.path.startswith(self._prefix)
        )

    async def scan(self, cursor: Mapping[str, Any]) -> ScanResult:
        queue, seen = deque(self._start), set(self._start)
        items: list[RemoteItem] = []
        while queue and len(items) < self._max:
            url = queue.popleft()
            if not await self._allowed(url):
                continue
            try:
                response = await self._http.get(
                    url, headers={"User-Agent": USER_AGENT}, timeout=20, follow_redirects=True
                )
            except httpx.HTTPError as exc:
                if url in self._start:
                    raise ConnectorError(f"{url} is unreachable: {exc}") from exc
                continue
            if response.status_code != 200:
                if url in self._start:
                    raise ConnectorError(f"{url} answered HTTP {response.status_code}")
                continue
            kind = response.headers.get("content-type", "").split(";")[0].strip()
            extension = EXTENSIONS.get(kind)
            if extension is None:
                continue
            body = response.content
            title = url
            if kind == "text/html":
                parser = _Links()
                parser.feed(response.text)
                title = parser.title.strip() or url
                for href in parser.links:
                    link = urldefrag(urljoin(url, href))[0]
                    if link not in seen and self._in_scope(link):
                        seen.add(link)
                        queue.append(link)
            self._cache[url] = body
            items.append(
                RemoteItem(
                    external_id=url,
                    title=title[:300],
                    filename=_filename(url, extension),
                    version=response.headers.get("etag") or hashlib.sha256(body).hexdigest()[:32],
                    url=url,
                )
            )
        # Cut short by max_pages: the pages beyond the limit were not seen, not deleted.
        return ScanResult(items=items, complete=not queue)

    async def fetch(self, item: RemoteItem) -> bytes:
        if item.external_id in self._cache:
            return self._cache[item.external_id]
        response = await self._http.get(
            item.external_id, headers={"User-Agent": USER_AGENT}, timeout=20
        )
        if response.status_code != 200:
            raise ConnectorError(f"{item.external_id} answered HTTP {response.status_code}")
        return response.content
