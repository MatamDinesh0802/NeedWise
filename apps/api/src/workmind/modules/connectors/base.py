"""The connector SDK (ARCHITECTURE section 10.2).

A connector answers two questions about its source:

    scan(cursor)  -> which items exist now, each with a version marker (etag, revision, mtime)
    fetch(item)   -> the item's bytes, as a file WorkMind can parse

The sync engine (sync.py) does everything else the same way for every connector: new and
changed items become document versions, items that disappeared are deleted (when the scan is
complete), and every document lands in the source's collection with that collection's access.
A connector never decides permissions.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


class ConnectorError(Exception):
    """The source refused or failed (bad credentials, rate limit, outage). The sync records it
    on the source and retries at the next interval; nothing is deleted on an error."""


@dataclass(frozen=True)
class RemoteItem:
    external_id: str  # stable for the item's whole life (a URL, a file id, a message ts)
    title: str
    filename: str  # decides the parser: ".md", ".pdf", ".json" (Slack export), ...
    version: str  # changes whenever the content does
    url: str | None = None
    modified_at: datetime | None = None
    meta: Mapping[str, Any] = field(default_factory=dict[str, Any])


@dataclass
class ScanResult:
    items: list[RemoteItem]
    # True when `items` is everything the source holds now: only then may the engine treat a
    # missing item as deleted. Incremental scans set it False.
    complete: bool = True
    cursor: dict[str, Any] = field(default_factory=dict[str, Any])
    # For an incomplete scan: True when it set out to list everything and was cut short (a page
    # limit, a truncated tree). Known items it did not list may be gone upstream: they are kept
    # and flagged "missing upstream" until a complete scan decides. False for a scan that only
    # looks at a window or at changes (Slack's history window, an incremental API): items
    # outside it are not suspects.
    truncated: bool = False


class Connector(Protocol):
    kind: str

    async def scan(self, cursor: Mapping[str, Any]) -> ScanResult: ...

    async def fetch(self, item: RemoteItem) -> bytes: ...
