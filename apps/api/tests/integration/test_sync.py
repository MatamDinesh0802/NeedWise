"""The sync engine against a scripted connector, with real PostgreSQL and Qdrant: adds, skips
unchanged items without fetching them, versions changed ones, propagates deletions only after a
complete scan, records failures without deleting anything, and runs one sync at a time.

Needs TEST_QDRANT_URL (and the test database)."""

import os
import uuid
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from tests.integration.conftest import SessionFactory, seed_acme
from workmind.core.tenancy import tenant_transaction
from workmind.modules.connectors.base import ConnectorError, RemoteItem, ScanResult
from workmind.modules.connectors.models import Source
from workmind.modules.connectors.secrets import SecretBox
from workmind.modules.connectors.sync import SyncDeps, sync_source
from workmind.modules.knowledge.dispatch import InlineDispatcher
from workmind.modules.knowledge.models import Collection, Document
from workmind.modules.knowledge.pipeline import IngestionDeps
from workmind.modules.knowledge.storage import LocalObjectStorage
from workmind.modules.retrieval.qdrant import QdrantClient, match
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

    def put(self, external_id: str, text: str, version: str) -> None:
        item = RemoteItem(
            external_id,
            external_id.title(),
            f"{external_id}.md",
            version,
            f"https://intranet/{external_id}",
        )
        self.items[external_id] = (item, f"# {external_id.title()}\n\n{text}\n".encode())

    async def scan(self, cursor: Mapping[str, Any]) -> ScanResult:
        if self.fail:
            raise ConnectorError("intranet answered HTTP 503")
        return ScanResult(
            [i for i, _ in self.items.values()], self.complete, {"seen": len(self.items)}
        )

    async def fetch(self, item: RemoteItem) -> bytes:
        self.fetched.append(item.external_id)
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
    remaining = await qdrant.count(name, {"must": [match("tenant_id", str(source.tenant_id))]})
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
