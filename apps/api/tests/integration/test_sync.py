"""The sync engine against a scripted connector, with real PostgreSQL and Qdrant: adds, skips
unchanged items without fetching them, versions changed ones, propagates deletions only after a
complete scan (except for documents under a legal hold) or when the source reports them (delta
tombstones), records failures without deleting anything (and without moving an incremental
source's cursor past them), keeps where an item sits in its source, and runs one sync at a
time. Every sync is a run in the history; a source that keeps failing alerts its managers once
per episode; items a cut-short scan no longer lists are flagged, not deleted; stale synced
documents remind their owners once per staleness episode. The last tests run the real
Confluence and SharePoint connectors against their documented-API fakes through the engine.

Needs TEST_QDRANT_URL (and the test database)."""

import os
import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select, update

from tests import fake_confluence
from tests.fake_confluence import FakeConfluence, Page
from tests.fake_graph import ROOT_ID, SITE_URL, FakeGraph, Item
from tests.integration.conftest import SessionFactory, seed_acme
from workmind.core.tenancy import tenant_transaction
from workmind.modules.connectors.base import ConnectorError, RemoteItem, ScanResult
from workmind.modules.connectors.confluence import ConfluenceConnector
from workmind.modules.connectors.models import Source, SourceRun
from workmind.modules.connectors.oauth import OAuthApp
from workmind.modules.connectors.reminders import StaleReminderReport, send_source_reminders
from workmind.modules.connectors.secrets import SecretBox
from workmind.modules.connectors.sharepoint import SharePointConnector
from workmind.modules.connectors.sync import SyncDeps, sync_source
from workmind.modules.governance.audit import Actor
from workmind.modules.governance.models import AuditEvent, LegalHold
from workmind.modules.identity.models import User
from workmind.modules.knowledge import document_acl
from workmind.modules.knowledge.dispatch import InlineDispatcher
from workmind.modules.knowledge.models import Collection, Document, DocumentVersion
from workmind.modules.knowledge.pipeline import IngestionDeps
from workmind.modules.knowledge.reindex import reindex_tenant
from workmind.modules.knowledge.storage import LocalObjectStorage, owned_key
from workmind.modules.retrieval.qdrant import QdrantClient
from workmind.modules.work.models import Notification, Task
from workmind.providers.embeddings import HashEmbeddings

QDRANT_URL = os.environ.get("TEST_QDRANT_URL")
pytestmark = pytest.mark.skipif(not QDRANT_URL, reason="set TEST_QDRANT_URL")
KEY = "c2VjcmV0LWtleS1mb3ItdGVzdHMtMzItYnl0ZXMtISE="


class ScriptedConnector:
    kind = "web"

    def __init__(self) -> None:
        self.items: dict[str, tuple[RemoteItem, bytes]] = {}
        self.complete = True
        self.truncated = False
        self.fail = False
        self.fetched: list[str] = []
        self.deleted: list[str] = []  # tombstones the next scan reports
        self.unreachable: set[str] = set()  # items whose download fails
        self.cursor: dict[str, Any] | None = None

    def put(
        self,
        external_id: str,
        text: str,
        version: str,
        meta: dict[str, Any] | None = None,
        modified_at: datetime | None = None,
    ) -> None:
        item = RemoteItem(
            external_id,
            external_id.title(),
            f"{external_id}.md",
            version,
            f"https://intranet/{external_id}",
            modified_at,
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
            truncated=self.truncated,
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


async def test_a_held_document_gone_from_the_source_stays(
    setup: tuple[SyncDeps, ScriptedConnector, Source, QdrantClient, str],
) -> None:
    deps, connector, source, qdrant, name = setup
    connector.put("leave", "Parental leave is 16 weeks.", "v1")
    connector.put("travel", "Hotels are capped at 250 euros.", "v1")
    await run(deps, source)
    held = (await documents(deps, source))["leave"]
    async with tenant_transaction(deps.sessions, source.tenant_id) as session:
        hold = LegalHold(
            tenant_id=source.tenant_id,
            matter="Smith v. Acme",
            reason="Preserve the leave policy as it was",
            target_type="document",
            target_id=held.id,
        )
        session.add(hold)
        await session.flush()
        hold_id = hold.id

    connector.items.clear()  # both pages were removed from the intranet
    first = await run(deps, source)
    second = await run(deps, source)

    left = await documents(deps, source)
    async with tenant_transaction(deps.sessions, source.tenant_id) as session:
        events = (
            await session.scalars(
                select(AuditEvent)
                .where(AuditEvent.action == "legal_hold.deletion_blocked")
                .order_by(AuditEvent.seq)
            )
        ).all()
    assert (first.deleted, first.kept_under_hold) == (1, 1)
    assert (second.deleted, second.kept_under_hold) == (0, 1)
    assert set(left) == {"leave"}
    assert await qdrant.count(name, tenant_id=source.tenant_id) == leave_chunks(left["leave"])
    # One event per sync that would have deleted it, naming the documents it kept.
    assert [(e.resource_type, e.details["via"]) for e in events] == [
        ("source", "connector"),
        ("source", "connector"),
    ]
    assert events[0].details["documents"] == {str(held.id): [str(hold_id)]}
    assert events[0].details["matters"] == ["Smith v. Acme"]


async def test_a_held_document_the_source_reports_deleted_stays(
    setup: tuple[SyncDeps, ScriptedConnector, Source, QdrantClient, str],
) -> None:
    """A delta tombstone (SharePoint) is a deletion too: a legal hold keeps the document."""
    deps, connector, source, _, _ = setup
    connector.put("leave", "Parental leave is 16 weeks.", "v1")
    connector.put("travel", "Hotels are capped at 250 euros.", "v1")
    await run(deps, source)
    held = (await documents(deps, source))["leave"]
    async with tenant_transaction(deps.sessions, source.tenant_id) as session:
        session.add(
            LegalHold(
                tenant_id=source.tenant_id,
                matter="Smith v. Acme",
                reason="Preserve the leave policy as it was",
                target_type="document",
                target_id=held.id,
            )
        )

    connector.complete = False  # an incremental scan: only the tombstones say what went
    connector.items.clear()
    connector.deleted = ["leave", "travel"]
    report = await run(deps, source)

    assert (report.deleted, report.kept_under_hold) == (1, 1)
    assert set(await documents(deps, source)) == {"leave"}


async def test_a_document_deleted_upstream_goes_once_its_hold_is_released(
    setup: tuple[SyncDeps, ScriptedConnector, Source, QdrantClient, str],
) -> None:
    """A delta feed reports a deletion once: the document a hold kept used to stay searchable
    for good after the release, with nothing showing it was gone upstream."""
    deps, connector, source, _, _ = setup
    connector.put("leave", "Parental leave is 16 weeks.", "v1")
    connector.put("travel", "Hotels are capped at 250 euros.", "v1")
    await run(deps, source)
    held = (await documents(deps, source))["leave"]
    async with tenant_transaction(deps.sessions, source.tenant_id) as session:
        hold = LegalHold(
            tenant_id=source.tenant_id,
            matter="Smith v. Acme",
            reason="Preserve the leave policy as it was",
            target_type="document",
            target_id=held.id,
        )
        session.add(hold)
    connector.complete = False  # incremental: the tombstone comes once
    connector.items.pop("leave")
    connector.deleted = ["leave"]
    tombstoned = await run(deps, source)
    connector.deleted = []
    while_held = await run(deps, source)
    kept = (await documents(deps, source))["leave"]

    async with tenant_transaction(deps.sessions, source.tenant_id) as session:
        await session.execute(
            update(LegalHold)
            .where(LegalHold.id == hold.id)
            .values(released_at=datetime.now(UTC), release_reason="Matter closed")
        )
    after_release = await run(deps, source)
    again = await run(deps, source)

    assert (tombstoned.deleted, tombstoned.kept_under_hold) == (0, 1)
    assert kept.source_deleted_at is not None
    assert (while_held.deleted, while_held.kept_under_hold) == (0, 1)  # still shown as kept
    assert (after_release.deleted, after_release.kept_under_hold) == (1, 0)
    assert (again.deleted, again.kept_under_hold) == (0, 0)
    assert set(await documents(deps, source)) == {"travel"}


async def test_a_held_item_that_comes_back_upstream_is_no_longer_marked_deleted(
    setup: tuple[SyncDeps, ScriptedConnector, Source, QdrantClient, str],
) -> None:
    deps, connector, source, _, _ = setup
    connector.put("leave", "Parental leave is 16 weeks.", "v1")
    await run(deps, source)
    held = (await documents(deps, source))["leave"]
    async with tenant_transaction(deps.sessions, source.tenant_id) as session:
        session.add(
            LegalHold(
                tenant_id=source.tenant_id,
                matter="Smith v. Acme",
                reason="Preserve",
                target_type="document",
                target_id=held.id,
            )
        )
    connector.complete = False
    saved = connector.items.pop("leave")
    connector.deleted = ["leave"]
    await run(deps, source)
    connector.deleted = []
    connector.items["leave"] = saved  # restored in the source
    await run(deps, source)
    assert (await documents(deps, source))["leave"].source_deleted_at is None


async def test_run_errors_name_the_item_by_its_id_never_its_title(
    setup: tuple[SyncDeps, ScriptedConnector, Source, QdrantClient, str],
) -> None:
    """Run errors are shown to everyone who manages the source, whom an item's own access list
    may leave out: "<title>: larger than 50 MB" showed the title of a file shared only with HR."""
    deps, connector, source, _, _ = setup
    connector.put("reorg-2027", "x" * 200, "v1")
    connector.items["reorg-2027"] = (
        replace(connector.items["reorg-2027"][0], title="Confidential reorg plan 2027"),
        connector.items["reorg-2027"][1],
    )
    report = await run(replace(deps, max_bytes=50), source)
    async with tenant_transaction(deps.sessions, source.tenant_id) as session:
        stored = await session.get(Source, source.id)
        (latest,) = (
            await session.scalars(select(SourceRun).where(SourceRun.source_id == source.id))
        ).all()
    assert report.failed == 1
    assert latest.errors == [{"item": "reorg-2027", "message": "larger than 0 MB"}]
    assert stored is not None
    assert "reorg plan" not in (stored.last_error or "")


async def test_wm_reindex_rebuilds_every_document_from_its_stored_original(
    setup: tuple[SyncDeps, ScriptedConnector, Source, QdrantClient, str],
) -> None:
    """`wm reindex <tenant>` (after changing the embedding model) was untested: every document
    is indexed again from storage, the index holds the same chunks, and a failure is counted
    without stopping the rest."""
    deps, connector, source, qdrant, name = setup
    connector.put("leave", "Parental leave is 16 weeks.", "v1")
    connector.put("travel", "Hotels are capped at 250 euros.", "v1")
    await run(deps, source)
    before = await qdrant.count(name, tenant_id=source.tenant_id)
    ingestion = IngestionDeps(deps.sessions, deps.storage, HashEmbeddings(32), qdrant, name)

    result = await reindex_tenant(ingestion, source.tenant_id)
    assert (result.documents, result.failed) == (2, 0)
    assert result.chunks == before
    assert await qdrant.count(name, tenant_id=source.tenant_id) == before

    # An original missing from storage fails that document only.
    travel = (await documents(deps, source))["travel"]
    async with tenant_transaction(deps.sessions, source.tenant_id) as session:
        version = await session.get(DocumentVersion, travel.current_version_id)
        assert version is not None
        await deps.storage.delete(owned_key(source.tenant_id, version.storage_key))
    partial = await reindex_tenant(ingestion, source.tenant_id)
    assert (partial.documents, partial.failed) == (1, 1)


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


# --- Run history, failure episodes, missing items, staleness --------------------------------


async def user_id(deps: SyncDeps, source: Source, name: str) -> uuid.UUID:
    async with tenant_transaction(deps.sessions, source.tenant_id) as session:
        return (await session.scalars(select(User.id).where(User.email.startswith(name)))).one()


async def stored_source(deps: SyncDeps, source: Source) -> Source:
    async with tenant_transaction(deps.sessions, source.tenant_id) as session:
        stored = await session.get(Source, source.id)
    assert stored is not None
    return stored


async def runs(deps: SyncDeps, source: Source) -> list[SourceRun]:
    async with tenant_transaction(deps.sessions, source.tenant_id) as session:
        return list(
            (
                await session.scalars(
                    select(SourceRun)
                    .where(SourceRun.source_id == source.id)
                    .order_by(SourceRun.started_at)
                )
            ).all()
        )


async def notifications(deps: SyncDeps, source: Source, kind: str) -> list[Notification]:
    async with tenant_transaction(deps.sessions, source.tenant_id) as session:
        return list(
            (
                await session.scalars(
                    select(Notification)
                    .where(Notification.kind == kind)
                    .order_by(Notification.created_at)
                )
            ).all()
        )


async def audit_actions(deps: SyncDeps, source: Source, prefix: str) -> list[str]:
    async with tenant_transaction(deps.sessions, source.tenant_id) as session:
        return list(
            (
                await session.scalars(
                    select(AuditEvent.action)
                    .where(AuditEvent.action.startswith(prefix))
                    .order_by(AuditEvent.seq)
                )
            ).all()
        )


async def test_every_sync_is_a_run_with_its_trigger_counts_and_errors(
    setup: tuple[SyncDeps, ScriptedConnector, Source, QdrantClient, str],
) -> None:
    deps, connector, source, _, _ = setup
    erin = await user_id(deps, source, "erin@")
    connector.put("leave", "Parental leave is 16 weeks.", "v1")

    await sync_source(deps, source.tenant_id, source.id, trigger="manual", requested_by=erin)
    await deps.ingestion.drain()  # type: ignore[attr-defined]
    connector.fail = True
    await run(deps, source)

    first, second = await runs(deps, source)
    assert (first.trigger, first.requested_by, first.status, first.complete) == (
        "manual",
        erin,
        "ok",
        True,
    )
    assert first.finished_at is not None
    assert first.finished_at >= first.started_at
    assert (first.stats["added"], first.errors) == (1, [])
    assert (second.trigger, second.requested_by, second.status) == ("schedule", None, "error")
    assert second.errors == [{"item": None, "message": "intranet answered HTTP 503"}]


async def test_a_worker_that_died_mid_sync_leaves_an_interrupted_run(
    setup: tuple[SyncDeps, ScriptedConnector, Source, QdrantClient, str],
) -> None:
    deps, connector, source, _, _ = setup
    connector.put("leave", "x", "v1")
    long_ago = datetime.now(UTC) - timedelta(hours=2)
    async with tenant_transaction(deps.sessions, source.tenant_id) as session:
        stored = await session.get(Source, source.id)
        assert stored is not None
        stored.status, stored.last_sync_at = "syncing", long_ago  # its worker died
        session.add(
            SourceRun(
                tenant_id=source.tenant_id,
                source_id=source.id,
                status="running",
                started_at=long_ago,
            )
        )

    await run(deps, source)

    dead, fresh = await runs(deps, source)
    assert dead.status == "error"
    assert dead.errors == [{"item": None, "message": "the worker stopped before the sync finished"}]
    assert fresh.status == "ok"


async def test_a_source_that_keeps_failing_alerts_once_per_episode_and_says_when_it_recovers(
    setup: tuple[SyncDeps, ScriptedConnector, Source, QdrantClient, str],
) -> None:
    deps, connector, source, _, _ = setup
    deps.failure_alert_after = 2
    erin, alice = await user_id(deps, source, "erin@"), await user_id(deps, source, "alice@")
    connector.put("leave", "x", "v1")
    connector.fail = True

    await run(deps, source)
    after_one = await notifications(deps, source, "source.failing")
    await run(deps, source)
    await run(deps, source)
    failing = await notifications(deps, source, "source.failing")
    during = await stored_source(deps, source)
    connector.fail = False
    await run(deps, source)
    recovered = await notifications(deps, source, "source.recovered")
    after = await stored_source(deps, source)
    connector.fail = True
    await run(deps, source)
    await run(deps, source)  # a new episode
    second_episode = await notifications(deps, source, "source.failing")

    assert after_one == []
    # The source's owner (an org admin) and the collection's owner, once for three failures.
    assert {n.user_id for n in failing} == {erin, alice}
    assert len(failing) == 2
    by_user = {n.user_id: n for n in failing}
    assert by_user[erin].link == "/admin/sources?source=intranet"
    assert by_user[alice].link == "/knowledge/sources?source=intranet"  # not an admin
    assert "2 syncs in a row failed" in by_user[erin].body
    assert "internal error" in by_user[erin].body  # the plain explanation, not the raw text
    assert (during.consecutive_failures, during.failure_alerted_at is not None) == (3, True)
    assert {n.user_id for n in recovered} == {erin, alice}
    assert (after.consecutive_failures, after.failure_alerted_at, after.status) == (0, None, "ok")
    assert len(second_episode) == 4
    # Each alert is recorded in the same transaction as the sync that crossed the threshold.
    assert await audit_actions(deps, source, "source.") == [
        "source.synced",
        "source.failing",
        "source.synced",
        "source.synced",
        "source.recovered",
        "source.synced",
        "source.synced",
        "source.failing",
        "source.synced",
    ]


async def test_a_cut_short_scan_flags_missing_items_and_only_a_complete_scan_deletes_them(
    setup: tuple[SyncDeps, ScriptedConnector, Source, QdrantClient, str],
) -> None:
    deps, connector, source, _, _ = setup
    connector.put("leave", "Parental leave is 16 weeks.", "v1")
    connector.put("travel", "Hotels are capped at 250 euros.", "v1")
    await run(deps, source)

    travel = connector.items.pop("travel")
    connector.complete, connector.truncated = False, True  # e.g. max_pages reached
    cut = await run(deps, source)
    flagged = (await documents(deps, source))["travel"].source_missing_since
    connector.items["travel"] = travel  # listed again
    back = await run(deps, source)
    cleared = (await documents(deps, source))["travel"].source_missing_since
    del connector.items["travel"]
    connector.truncated = False  # a window (Slack history): unlisted items are not suspects
    windowed = await run(deps, source)
    not_suspect = (await documents(deps, source))["travel"].source_missing_since
    connector.complete = True
    complete = await run(deps, source)

    assert (cut.deleted, cut.missing, flagged is not None) == (0, 1, True)
    assert (back.missing, cleared) == (0, None)
    assert (windowed.missing, not_suspect) == (0, None)
    assert (complete.deleted, set(await documents(deps, source))) == (1, {"leave"})
    history = await runs(deps, source)
    assert [r.complete for r in history] == [True, False, False, False, True]
    assert history[1].stats["missing"] == 1


async def test_new_content_restarts_the_staleness_clock_with_the_sources_own_date(
    setup: tuple[SyncDeps, ScriptedConnector, Source, QdrantClient, str],
) -> None:
    deps, connector, source, _, _ = setup
    edited = datetime(2026, 3, 2, 8, 30, tzinfo=UTC)
    connector.put("leave", "Parental leave is 16 weeks.", "v1", modified_at=edited)
    connector.put("travel", "Hotels are capped at 250 euros.", "v1")  # no date from the source
    before = datetime.now(UTC)
    await run(deps, source)
    first = await documents(deps, source)
    connector.put("leave", "Parental leave is 18 weeks.", "v2", modified_at=None)
    await run(deps, source)
    second = await documents(deps, source)

    assert first["leave"].source_changed_at == edited
    travel_changed = first["travel"].source_changed_at
    assert travel_changed is not None
    assert travel_changed >= before
    leave_changed = second["leave"].source_changed_at
    assert leave_changed is not None
    assert leave_changed > edited
    assert second["travel"].source_changed_at == travel_changed  # unchanged: same clock


async def age(deps: SyncDeps, source: Source, external_id: str, days: int) -> None:
    async with tenant_transaction(deps.sessions, source.tenant_id) as session:
        await session.execute(
            update(Document)
            .where(Document.source_id == source.id, Document.external_id == external_id)
            .values(source_changed_at=datetime.now(UTC) - timedelta(days=days))
        )


async def reminders(deps: SyncDeps, source: Source) -> StaleReminderReport:
    return await send_source_reminders(deps.sessions, source.tenant_id, failure_alert_after=3)


async def test_stale_documents_remind_their_owner_once_per_staleness_episode(
    setup: tuple[SyncDeps, ScriptedConnector, Source, QdrantClient, str],
) -> None:
    deps, connector, source, _, _ = setup
    erin = await user_id(deps, source, "erin@")
    async with tenant_transaction(deps.sessions, source.tenant_id) as session:
        stored = await session.get(Source, source.id)
        assert stored is not None
        stored.stale_after_days = 30
    for name in ("leave", "travel", "expenses"):
        connector.put(name, f"The {name} policy.", "v1")
    await run(deps, source)
    await age(deps, source, "leave", 40)
    await age(deps, source, "travel", 45)
    await age(deps, source, "expenses", 10)  # fresh enough

    first = await reminders(deps, source)
    again = await reminders(deps, source)  # the same day, or a retry: nothing new
    async with tenant_transaction(deps.sessions, source.tenant_id) as session:
        tasks = (await session.scalars(select(Task).where(Task.assignee_id == erin))).all()
    notes = await notifications(deps, source, "source.stale_documents")
    docs = await documents(deps, source)
    connector.put("leave", "The leave policy, updated.", "v2")  # changed in the source
    await run(deps, source)
    after_change = (await documents(deps, source))["leave"].stale_reminded_at
    await age(deps, source, "leave", 31)  # a month later, unchanged again: a new episode
    second = await reminders(deps, source)

    assert set(first.reminded) == {docs["leave"].id, docs["travel"].id}
    assert (again.reminded, again.tasks) == ([], [])
    assert len(tasks) == 1
    assert len(notes) == 1
    task = tasks[0]
    assert task.title == "Check 2 synced documents that look stale"
    assert f"/knowledge/documents/{docs['leave'].id}" in task.description
    assert "unchanged in the source since" in task.description
    assert task.origin["via"] == "connectors"
    assert {d["id"] for d in task.origin["documents"]} == {
        str(docs["leave"].id),
        str(docs["travel"].id),
    }
    assert notes[0].user_id == erin
    assert notes[0].link == f"/tasks/{task.id}"
    assert docs["expenses"].stale_reminded_at is None
    assert after_change is None
    assert second.reminded == [docs["leave"].id]
    assert (
        await audit_actions(deps, source, "knowledge.document.stale_reminded")
        == ["knowledge.document.stale_reminded"] * 3
    )


async def test_missing_upstream_documents_are_stale_without_a_threshold_until_they_return(
    setup: tuple[SyncDeps, ScriptedConnector, Source, QdrantClient, str],
) -> None:
    deps, connector, source, _, _ = setup
    async with tenant_transaction(deps.sessions, source.tenant_id) as session:
        stored = await session.get(Source, source.id)
        assert stored is not None
        stored.stale_after_days = None  # no "not updated" reminders
    connector.put("leave", "x", "v1")
    connector.put("travel", "y", "v1")
    await run(deps, source)
    await age(deps, source, "leave", 900)
    travel = connector.items.pop("travel")
    connector.complete, connector.truncated = False, True
    await run(deps, source)

    flagged = await reminders(deps, source)
    connector.items["travel"] = travel
    await run(deps, source)
    ended = await reminders(deps, source)

    docs = await documents(deps, source)
    assert flagged.reminded == [docs["travel"].id]  # not "leave": no threshold
    assert ended.episodes_ended == 1
    assert docs["travel"].stale_reminded_at is None


async def test_an_owner_who_cannot_open_the_collection_is_not_reminded(
    setup: tuple[SyncDeps, ScriptedConnector, Source, QdrantClient, str],
) -> None:
    deps, connector, source, _, _ = setup
    async with tenant_transaction(deps.sessions, source.tenant_id) as session:
        hr = (
            await session.scalars(select(Collection).where(Collection.key == "hr-guidance"))
        ).one()
        stored = await session.get(Source, source.id)
        assert stored is not None
        stored.collection_id, stored.stale_after_days = hr.id, 30  # Erin cannot open HR Guidance
    connector.put("leave", "x", "v1")
    await run(deps, source)
    await age(deps, source, "leave", 60)

    report = await reminders(deps, source)

    assert (report.reminded, report.without_owner) == ([], 1)
    assert await notifications(deps, source, "source.stale_documents") == []


async def test_an_owner_left_out_of_a_documents_own_list_is_not_reminded(
    setup: tuple[SyncDeps, ScriptedConnector, Source, QdrantClient, str],
) -> None:
    """Permission sync (docs/permission-sync.md) can give a synced document a list that leaves
    its owner out: its title must not reach them in a reminder."""
    deps, connector, source, _, _ = setup
    alice = await user_id(deps, source, "alice@")
    async with tenant_transaction(deps.sessions, source.tenant_id) as session:
        stored = await session.get(Source, source.id)
        assert stored is not None
        stored.stale_after_days = 30
    connector.put("leave", "x", "v1")
    connector.put("travel", "y", "v1")
    await run(deps, source)
    await age(deps, source, "leave", 60)
    await age(deps, source, "travel", 60)
    async with tenant_transaction(deps.sessions, source.tenant_id) as session:
        leave = (
            await session.scalars(
                select(Document).where(
                    Document.source_id == source.id, Document.external_id == "leave"
                )
            )
        ).one()
        await document_acl.set_acl(
            session,
            source.tenant_id,
            leave,
            [document_acl.AclEntry(f"user:{alice}")],  # Alice only, not Erin (the owner)
            origin="source",
            actor=Actor.system(),
        )

    report = await reminders(deps, source)

    docs = await documents(deps, source)
    assert (report.reminded, report.without_owner) == ([docs["travel"].id], 1)
    notes = await notifications(deps, source, "source.stale_documents")
    assert [n.title for n in notes] == ["Check “Travel”: it looks stale"]


async def test_an_archived_synced_document_is_never_stale(
    setup: tuple[SyncDeps, ScriptedConnector, Source, QdrantClient, str],
) -> None:
    """Document retention can archive a synced document: it is out of use, so neither the
    reminders nor the Sources page count it as stale (docs/enterprise.md#document-retention)."""
    deps, connector, source, _, _ = setup
    async with tenant_transaction(deps.sessions, source.tenant_id) as session:
        stored = await session.get(Source, source.id)
        assert stored is not None
        stored.stale_after_days = 30
    connector.put("leave", "x", "v1")
    connector.put("travel", "y", "v1")
    await run(deps, source)
    await age(deps, source, "leave", 60)
    await age(deps, source, "travel", 60)
    async with tenant_transaction(deps.sessions, source.tenant_id) as session:
        await session.execute(
            update(Document)
            .where(Document.source_id == source.id, Document.external_id == "leave")
            .values(archived_at=datetime.now(UTC))
        )

    report = await reminders(deps, source)

    docs = await documents(deps, source)
    assert report.reminded == [docs["travel"].id]


async def test_the_daily_task_alerts_a_failing_source_the_engine_did_not(
    setup: tuple[SyncDeps, ScriptedConnector, Source, QdrantClient, str],
) -> None:
    """E.g. the threshold was lowered after the failures."""
    deps, connector, source, _, _ = setup
    deps.failure_alert_after = 5
    connector.fail = True
    await run(deps, source)
    await run(deps, source)
    before = await notifications(deps, source, "source.failing")

    report = await reminders(deps, source)  # threshold 3: two failures are not enough
    async with tenant_transaction(deps.sessions, source.tenant_id) as session:
        stored = await session.get(Source, source.id)
        assert stored is not None
        stored.consecutive_failures = 3
    later = await reminders(deps, source)
    twice = await reminders(deps, source)

    assert before == []
    assert report.failing_alerted == []
    assert (later.failing_alerted, twice.failing_alerted) == (["intranet"], [])
    assert len(await notifications(deps, source, "source.failing")) == 2  # Erin and Alice


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
