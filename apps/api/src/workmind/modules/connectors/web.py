"""Web crawler: pages under a start URL, on the same host, honouring robots.txt.

Pages are attacker-controlled content, and so are their redirects. Every host is checked
against the outbound URL policy (core/urls.py: no cloud metadata, link-local or reserved
addresses, also through a name) before the first request to it, and redirects are followed by
hand, one hop at a time, only while they stay on the site: a page that redirects elsewhere is
skipped, so a public page cannot send the crawler to an internal address and get the answer
indexed as a document.
"""

import hashlib
import re
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urldefrag, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from workmind.core.urls import UnsafeUrlError, check_url_resolved
from workmind.modules.connectors.base import ConnectorError, RemoteItem, ScanResult

USER_AGENT = "WorkMindBot/0.6 (+https://github.com/workmind-ai)"
EXTENSIONS = {
    "text/html": ".html",
    "application/pdf": ".pdf",
    "text/markdown": ".md",
    "text/plain": ".txt",
}
MAX_REDIRECTS = 5
UrlCheck = Callable[[str], Awaitable[object]]


class _OffSite(Exception):  # noqa: N818  (control flow, not an error)
    """A redirect left the site: the page is not followed."""

    def __init__(self, target: str) -> None:
        super().__init__(target)
        self.target = target


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


def _last_modified(value: str | None) -> datetime | None:
    """The Last-Modified header, when the server sends a valid one."""
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


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
        check: UrlCheck = check_url_resolved,
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
        self._check = check
        self._checked: set[str] = set()  # hosts that passed the outbound URL policy

    async def _allowed_host(self, url: str) -> None:
        """Raises UnsafeUrlError unless the URL's host passes the outbound URL policy (resolved
        once per host and connector)."""
        host = (urlsplit(url).hostname or "").lower()
        if host not in self._checked:
            await self._check(url)
            self._checked.add(host)

    async def _get(self, url: str) -> tuple[httpx.Response, str]:
        """GET with redirects followed by hand while they stay in scope. Returns the response
        and the URL that answered. Raises _OffSite, UnsafeUrlError or httpx.HTTPError."""
        hops = 0
        while True:
            await self._allowed_host(url)
            response = await self._http.get(
                url, headers={"User-Agent": USER_AGENT}, timeout=20, follow_redirects=False
            )
            location = response.headers.get("location")
            if not response.is_redirect or not location:
                return response, url
            hops += 1
            if hops > MAX_REDIRECTS:
                raise httpx.TooManyRedirects(
                    f"more than {MAX_REDIRECTS} redirects", request=response.request
                )
            target = urldefrag(urljoin(url, location))[0]
            if not self._in_scope(target):
                raise _OffSite(target)
            url = target

    async def _allowed(self, url: str) -> bool:
        if self._robots is None:
            robots = RobotFileParser()
            base = urlsplit(self._start[0])
            try:
                await self._allowed_host(self._start[0])
                response = await self._http.get(
                    f"{base.scheme}://{base.netloc}/robots.txt", timeout=10, follow_redirects=False
                )
                robots.parse(response.text.splitlines() if response.status_code == 200 else [])
            except (httpx.HTTPError, UnsafeUrlError):
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
            start = url in self._start
            if not await self._allowed(url):
                continue
            try:
                response, answered = await self._get(url)
            except UnsafeUrlError as exc:
                if start:
                    raise ConnectorError(f"{url} is not allowed: {exc}") from exc
                continue
            except _OffSite as exc:
                if start:
                    raise ConnectorError(
                        f"{url} redirects to another site ({urlsplit(exc.target).netloc}); "
                        "not followed"
                    ) from exc
                continue
            except httpx.HTTPError as exc:
                if start:
                    raise ConnectorError(f"{url} is unreachable: {exc}") from exc
                continue
            if answered != url:  # an in-scope redirect: index the page under its own URL
                if answered in seen:
                    continue
                seen.add(answered)
                url = answered
            if response.status_code != 200:
                if start:
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
                    modified_at=_last_modified(response.headers.get("last-modified")),
                )
            )
        # Cut short by max_pages: the pages beyond the limit were not seen, not deleted.
        return ScanResult(items=items, complete=not queue, truncated=bool(queue))

    async def fetch(self, item: RemoteItem) -> bytes:
        if item.external_id in self._cache:
            return self._cache[item.external_id]
        try:
            response, _ = await self._get(item.external_id)
        except UnsafeUrlError as exc:
            raise ConnectorError(f"{item.external_id} is not allowed: {exc}") from exc
        except _OffSite as exc:
            raise ConnectorError(f"{item.external_id} redirects to another site") from exc
        except httpx.HTTPError as exc:
            raise ConnectorError(f"{item.external_id} is unreachable: {exc}") from exc
        if response.status_code != 200:
            raise ConnectorError(f"{item.external_id} answered HTTP {response.status_code}")
        return response.content
