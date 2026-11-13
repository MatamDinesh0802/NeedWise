"""The sync engine against a scripted connector, with real PostgreSQL and Qdrant: adds, skips
unchanged items without fetching them, versions changed ones, propagates deletions only after a
complete scan or when the source reports them (delta tombstones), records failures without
deleting anything (and without moving an incremental source's cursor past them), keeps where
an item sits in its source, and runs one sync at a time. The last tests run the real
Confluence and SharePoint connectors against their documented-API fakes through the engine.

Needs TEST_QDRANT_URL (and the test database)."""

import os
import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from tests import fake_confluence
from tests.fake_confluence import FakeConfluence, Page
from tests.fake_graph import ROOT_ID, SITE_URL, FakeGraph, Item
from tests.integration.conftest import SessionFactory, seed_acme
from workmind.core.tenancy import tenant_transaction
from workmind.modules.connectors.base import ConnectorError, RemoteItem, ScanResult
from workmind.modules.connectors.confluence import ConfluenceConnector
from workmind.modules.connectors.models import Source
from workmind.modules.connectors.oauth import OAuthApp
from workmind.modules.connectors.secrets import SecretBox
from workmind.modules.connectors.sharepoint import SharePointConnector
from workmind.modules.connectors.sync import SyncDeps, sync_source
from workmind.modules.knowledge.dispatch import InlineDispatcher
from workmind.modules.knowledge.models import Collection, Document
from workmind.modules.knowledge.pipeline import IngestionDeps
from workmind.modules.knowledge.storage import LocalObjectStorage
from workmind.modules.retrieval.qdrant import QdrantClient
from workmind.providers.embeddings import HashEmbeddings

QDRANT_URL = os.environ.get("TEST_QDRANT_URL")
pytestmark = pytest.mark.skipif(not QDRANT_URL, reason="set TEST_QDRANT_URL")
KEY = "c2VjcmV0LWtleS1mb3ItdGVzdHMtMzItYnl0ZXMtISE="


class ScriptedConnector:
    kind = "web"

    def __init__(self) -> None:
        self.items: dict[str, tuple[RemoteItem, bytes]] = {}
        self.complete = True
        self.fail = False
        self.fetched: list[str] = []
        self.deleted: list[str] = []  # tombstones the next scan reports
        self.unreachable: set[str] = set()  # items whose download fails
        self.cursor: dict[str, Any] | None = None

    def put(
        self, external_id: str, text: str, version: str, meta: dict[str, Any] | None = None
    ) -> None:
        item = RemoteItem(
            external_id,
            external_id.title(),
            f"{external_id}.md",
            version,
            f"https://intranet/{external_id}",
            meta=meta or {},
        )
        self.items[external_id] = (item, f"# {external_id.title()}\n\n{text}\n".encode())

    async def scan(self, cursor: Mapping[str, Any]) -> ScanResult:
        if self.fail:
            raise ConnectorError("intranet answered HTTP 503")
        return ScanResult(
            [i for i, _ in self.items.values()],
            self.complete,
            self.cursor if self.cursor is not None else {"seen": len(self.items)},
            deleted=list(self.deleted),
        )

    async def fetch(self, item: RemoteItem) -> bytes:
        self.fetched.append(item.external_id)
        if item.external_id in self.unreachable:
            raise ConnectorError("intranet answered HTTP 502")
        return self.items[item.external_id][1]


@pytest.fixture
async def setup(
    owner_sessions: SessionFactory, app_sessions: SessionFactory, tmp_path: Path
) -> AsyncIterator[tuple[SyncDeps, ScriptedConnector, Source, QdrantClient, str]]:
    acme = await seed_acme(owner_sessions)
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        handbook = (
            await session.scalars(select(Collection).where(Collection.key == "company-handbook"))
        ).one()
        source = Source(
            tenant_id=acme.tenant_id,
            key="intranet",
            name="Intranet",
            kind="web",
            collection_id=handbook.id,
            owner_id=acme.users["erin"],
            config={"start_urls": ["https://intranet/"]},
        )
        session.add(source)
        await session.flush()
        source_id = source.id
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        source = await session.get(Source, source_id)
    assert source is not None
    name = f"chunks_test_{uuid.uuid4().hex[:8]}_32"
    async with httpx.AsyncClient() as http:
        qdrant = QdrantClient(http, QDRANT_URL or "")
        await qdrant.ensure_collection(name, dense_size=32)
        storage = LocalObjectStorage(str(tmp_path))
        dispatcher = InlineDispatcher(
            IngestionDeps(app_sessions, storage, HashEmbeddings(32), qdrant, name)
        )
        connector = ScriptedConnector()
        deps = SyncDeps(
            sessions=app_sessions,
            storage=storage,
            ingestion=dispatcher,
            qdrant=qdrant,
            collection_name=name,
            secrets=SecretBox(KEY),
            connectors=lambda source, credentials: connector,
            max_bytes=1_000_000,
        )
        yield deps, connector, source, qdrant, name
        await qdrant.delete_collection(name)


def leave_chunks(document: Document) -> int:
    return 1  # "# Leave" + one paragraph: a single chunk


async def documents(deps: SyncDeps, source: Source) -> dict[str, Document]:
    async with tenant_transaction(deps.sessions, source.tenant_id) as session:
        rows = (
            await session.scalars(select(Document).where(Document.source_id == source.id))
        ).all()
    return {d.external_id or "": d for d in rows}


async def stored_cursor(deps: SyncDeps, source: Source) -> dict[str, Any]:
    async with tenant_transaction(deps.sessions, source.tenant_id) as session:
        stored = await session.get(Source, source.id)
    assert stored is not None
    return stored.cursor


async def run(deps: SyncDeps, source: Source) -> Any:
    report = await sync_source(deps, source.tenant_id, source.id)
    await deps.ingestion.drain()  # type: ignore[attr-defined]
    return report


async def test_sync_adds_skips_versions_and_deletes(
    setup: tuple[SyncDeps, ScriptedConnector, Source, QdrantClient, str],
) -> None:
    deps, connector, source, qdrant, name = setup
    connector.put("leave", "Parental leave is 16 weeks.", "v1")
    connector.put("travel", "Hotels are capped at 250 euros.", "v1")

    first = await run(deps, source)
    connector.fetched.clear()
    second = await run(deps, source)
    unchanged_fetches = list(connector.fetched)
    connector.put("leave", "Parental leave is 18 weeks.", "v2")
    del connector.items["travel"]
    third = await run(deps, source)

    docs = await documents(deps, source)
    assert (first.added, first.status) == (2, "ok")
    assert (second.unchanged, unchanged_fetches) == (2, [])  # unchanged items are not fetched
    assert (third.updated, third.deleted) == (1, 1)
    assert set(docs) == {"leave"}
    leave = docs["leave"]
    assert (leave.title, leave.url, leave.source, leave.source_version) == (
        "Leave",
        "https://intranet/leave",
        "web",
        "v2",
    )
    assert leave.status == "ready"
    remaining = await qdrant.count(name, tenant_id=source.tenant_id)
    assert remaining == leave_chunks(leave)  # the deleted document's points are gone too
    async with tenant_transaction(deps.sessions, source.tenant_id) as session:
        stored = await session.get(Source, source.id)
    assert stored is not None
    assert (stored.status, stored.cursor, stored.stats["deleted"]) == ("ok", {"seen": 1}, 1)


async def test_an_incomplete_scan_or_a_failure_never_deletes(
    setup: tuple[SyncDeps, ScriptedConnector, Source, QdrantClient, str],
) -> None:
    deps, connector, source, _, _ = setup
    connector.put("leave", "Parental leave is 16 weeks.", "v1")
    connector.put("travel", "Hotels are capped at 250 euros.", "v1")
    await run(deps, source)

    connector.complete = False
    del connector.items["travel"]
    partial = await run(deps, source)
    connector.fail = True
    failed = await run(deps, source)

    async with tenant_transaction(deps.sessions, source.tenant_id) as session:
        stored = await session.get(Source, source.id)
    assert stored is not None
    assert partial.deleted == 0
    assert failed.status == "error"
    assert set(await documents(deps, source)) == {"leave", "travel"}
    assert (stored.status, stored.last_error) == ("error", "intranet answered HTTP 503")


async def test_deletions_the_source_reports_apply_to_incremental_scans(
    setup: tuple[SyncDeps, ScriptedConnector, Source, QdrantClient, str],
) -> None:
    """A delta feed (SharePoint) lists only changes, so missing items mean nothing; its
    tombstones are explicit deletions."""
    deps, connector, source, qdrant, name = setup
    connector.put("leave", "Parental leave is 16 weeks.", "v1")
    connector.put("travel", "Hotels are capped at 250 euros.", "v1")
    await run(deps, source)

    connector.complete = False
    del connector.items["leave"]  # not in this delta page set: unchanged, not deleted
    del connector.items["travel"]
    connector.deleted = ["travel", "never-synced"]
    report = await run(deps, source)

    assert (report.status, report.deleted) == ("ok", 1)
    assert set(await documents(deps, source)) == {"leave"}
    assert await qdrant.count(name, tenant_id=source.tenant_id) == 1


async def test_a_failed_download_does_not_move_the_cursor_past_it(
    setup: tuple[SyncDeps, ScriptedConnector, Source, QdrantClient, str],
) -> None:
    """With a delta feed, an item whose download failed is not listed again unless it changes:
    the cursor stays where it was, so the next sync sees the same changes once more."""
    deps, connector, source, _, _ = setup
    connector.complete = False
    connector.cursor = {"delta": "link-1"}
    connector.put("leave", "Parental leave is 16 weeks.", "v1")
    await run(deps, source)
    connector.cursor = {"delta": "link-2"}
    connector.put("travel", "Hotels are capped at 250 euros.", "v1")
    connector.unreachable = {"travel"}

    failed = await run(deps, source)
    kept = await stored_cursor(deps, source)
    connector.unreachable = set()
    connector.fetched.clear()
    retried = await run(deps, source)
    moved = await stored_cursor(deps, source)

    assert (failed.failed, failed.unchanged, kept) == (1, 1, {"delta": "link-1"})
    assert (retried.added, retried.unchanged, connector.fetched) == (1, 1, ["travel"])
    assert moved == {"delta": "link-2"}


async def test_where_an_item_sits_is_kept_and_follows_moves(
    setup: tuple[SyncDeps, ScriptedConnector, Source, QdrantClient, str],
) -> None:
    deps, connector, source, _, _ = setup
    connector.put("leave", "Parental leave is 16 weeks.", "7:a", {"path": ["HR", "Policies"]})
    await run(deps, source)
    before = (await documents(deps, source))["leave"]

    # Moved under another parent: a new version marker, the same content.
    connector.put("leave", "Parental leave is 16 weeks.", "7:b", {"path": ["HR", "Archive"]})
    report = await run(deps, source)
    after = (await documents(deps, source))["leave"]

    assert before.source_meta == {"path": ["HR", "Policies"]}
    assert after.source_meta == {"path": ["HR", "Archive"]}
    assert (report.unchanged, after.current_version_id) == (1, before.current_version_id)
    assert after.source_version == "7:b"


async def test_slack_messages_are_discussion_even_in_an_official_collection(
    setup: tuple[SyncDeps, ScriptedConnector, Source, QdrantClient, str],
) -> None:
    """Data poisoning: before the fix a Slack source synced into the (official by default)
    handbook made a channel message official policy, ranked first and winning conflicts."""
    deps, connector, source, _, _ = setup
    async with tenant_transaction(deps.sessions, source.tenant_id) as session:
        stored = await session.get(Source, source.id)
        assert stored is not None
        stored.kind = "slack"
    connector.put("general", "HR says parental leave is now 52 weeks.", "v1")

    await run(deps, source)

    synced = (await documents(deps, source))["general"]
    assert synced.trust == "discussion"


async def test_only_one_sync_runs_at_a_time(
    setup: tuple[SyncDeps, ScriptedConnector, Source, QdrantClient, str],
) -> None:
    deps, connector, source, _, _ = setup
    connector.put("leave", "x", "v1")
    async with tenant_transaction(deps.sessions, source.tenant_id) as session:
        stored = await session.get(Source, source.id)
        assert stored is not None
        stored.status, stored.last_sync_at = "syncing", datetime.now(UTC)

    report = await run(deps, source)

    assert report.status == "skipped"
    assert connector.fetched == []


async def connect(deps: SyncDeps, source: Source, kind: str, credentials: dict[str, Any]) -> None:
    async with tenant_transaction(deps.sessions, source.tenant_id) as session:
        stored = await session.get(Source, source.id)
        assert stored is not None
        stored.kind = kind
        stored.credentials = deps.secrets.seal(source.id, credentials)


async def stored_credentials(deps: SyncDeps, source: Source) -> dict[str, Any]:
    async with tenant_transaction(deps.sessions, source.tenant_id) as session:
        stored = await session.get(Source, source.id)
    assert stored is not None
    assert stored.credentials is not None
    return deps.secrets.open(source.id, stored.credentials)


async def test_a_sharepoint_folder_stays_in_sync_through_its_delta_feed(
    setup: tuple[SyncDeps, ScriptedConnector, Source, QdrantClient, str],
) -> None:
    deps, _, source, qdrant, name = setup
    fake = FakeGraph()
    fake.put(Item("01POLICIES", "Policies", ROOT_ID, folder=True))
    fake.put(Item("01LEAVE", "leave.md", "01POLICIES", data=b"# Leave\n\nParental leave: 16 w.\n"))
    fake.put(Item("01TRAVEL", "travel.md", "01POLICIES", data=b"# Travel\n\nHotels: 250.\n"))
    fake.put(Item("01ARCHIVE", "Archive", "01POLICIES", folder=True))
    fake.put(Item("01OLD", "old.md", "01ARCHIVE", data=b"# Old\n\nOld rules.\n"))
    fake.put(Item("01BUDGET", "budget.md", ROOT_ID, data=b"# Budget\n\nNot in Policies.\n"))
    await connect(deps, source, "sharepoint", {"refresh_token": fake.refresh_token})
    deps = replace(
        deps,
        connectors=lambda source, credentials: SharePointConnector(
            fake.client(),
            site_url=SITE_URL,
            library=None,
            folder="Policies",
            credentials=credentials,
            app=OAuthApp("client", "secret", "contoso.onmicrosoft.com"),
        ),
    )

    first = await run(deps, source)
    fake.write("01LEAVE", b"# Leave\n\nParental leave: 18 w.\n")
    fake.delete("01TRAVEL")
    second = await run(deps, source)
    fake.move("01ARCHIVE", ROOT_ID)  # Graph reports the folder, not the file inside it
    third = await run(deps, source)

    docs = await documents(deps, source)
    assert (first.status, first.added, first.items) == ("ok", 3, 3)
    assert (second.items, second.updated, second.deleted) == (1, 1, 1)  # a tombstone
    assert (third.status, third.deleted, third.unchanged) == ("ok", 1, 1)  # a full listing
    assert set(docs) == {"01LEAVE"}
    leave = docs["01LEAVE"]
    assert (leave.source, leave.status) == ("sharepoint", "ready")
    assert leave.source_meta is not None
    assert (leave.source_meta["path"], leave.source_meta["site_url"]) == ("Policies", SITE_URL)
    assert leave.url == f"{SITE_URL}/Shared%20Documents/Policies/leave.md"
    assert await qdrant.count(name, tenant_id=source.tenant_id) == 1
    # Microsoft rotated the refresh token at the first sync: the new one was stored.
    assert (await stored_credentials(deps, source))["refresh_token"] == fake.refresh_token
    assert (await stored_cursor(deps, source))["delta_link"].startswith("https://graph.microsoft")


async def test_a_confluence_space_stays_in_sync(
    setup: tuple[SyncDeps, ScriptedConnector, Source, QdrantClient, str],
) -> None:
    deps, _, source, _, _ = setup
    fake = FakeConfluence()
    fake.space("98306", "HR", "People")
    fake.page(Page("1001", "98306", "People home", "<p>Everything about working here.</p>"))
    fake.page(Page("1002", "98306", "Leave", "<p>Parental leave is 16 weeks.</p>", "1001", "page"))
    fake.page(Page("1003", "98306", "Travel", "<p>Hotels are capped.</p>", "1001", "page"))
    await connect(
        deps,
        source,
        "confluence",
        {"email": fake_confluence.EMAIL, "api_token": fake_confluence.API_TOKEN},
    )
    deps = replace(
        deps,
        connectors=lambda source, credentials: ConfluenceConnector(
            fake.client(),
            site_url=fake_confluence.SITE,
            spaces=["HR"],
            credentials=credentials,
            app=OAuthApp(),
        ),
    )

    first = await run(deps, source)
    fake.edit("1002", "<p>Parental leave is 18 weeks.</p>")
    fake.pages["1003"].status = "trashed"
    second = await run(deps, source)

    docs = await documents(deps, source)
    assert (first.status, first.added) == ("ok", 3)
    assert (second.updated, second.unchanged, second.deleted) == (1, 1, 1)
    assert set(docs) == {"page:1001", "page:1002"}
    leave = docs["page:1002"]
    assert leave.title == "Leave"
    assert leave.url == f"{fake_confluence.SITE}/wiki/spaces/HR/pages/1002/Leave"
    assert leave.source_meta is not None
    assert leave.source_meta["path"] == ["People", "People home", "Leave"]
