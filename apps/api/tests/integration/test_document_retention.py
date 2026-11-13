"""Document retention per collection (Enterprise, part of "retention_policies";
docs/enterprise.md#document-retention), with and without a license.

Without one, the routes answer 402. With one: rules of at least 30 days for collections of this
organization; a preview whose counts are exactly what a run then deletes or archives; legal holds,
official documents and pending official requests kept (and, for deletion, synced documents and
meeting transcripts), each counted with its reason; archived documents out of search until an
editor restores them or new content arrives. Needs the database, TEST_VALKEY_URL and
TEST_QDRANT_URL.
"""

import asyncio
import os
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from tests.integration.conftest import VALKEY_URL, Acme, Database, SessionFactory
from workmind.core.config import Settings
from workmind.core.edition import FEATURES, Edition, License
from workmind.core.extensions import ExtensionRegistry, enable_extensions
from workmind.core.tenancy import tenant_transaction
from workmind.main import create_app
from workmind.modules.connectors.models import Source
from workmind.modules.governance.audit import Actor
from workmind.modules.governance.models import AuditEvent, LegalHold
from workmind.modules.knowledge.governance import OFFICIAL_KIND
from workmind.modules.knowledge.models import Collection, Document, DocumentVersion
from workmind.modules.knowledge.storage import LocalObjectStorage
from workmind.modules.meetings.models import Meeting
from workmind.modules.retrieval.qdrant import QdrantClient, match
from workmind.modules.work.models import Approval

QDRANT_URL = os.environ.get("TEST_QDRANT_URL")
pytestmark = pytest.mark.skipif(
    not (VALKEY_URL and QDRANT_URL), reason="set TEST_VALKEY_URL and TEST_QDRANT_URL"
)
OLD = timedelta(days=45)  # older than a 30-day rule
RECENT = timedelta(days=10)
ROUTES = (
    ("GET", "/api/v1/admin/retention/documents", None),
    ("PUT", "/api/v1/admin/retention/documents", {"rules": []}),
    ("POST", "/api/v1/admin/retention/documents/preview", None),
    ("POST", "/api/v1/admin/retention/documents/run", {"confirm": "delete"}),
)
NOTE = b"# Dock safety\n\n## Forklifts\nForklifts stop at the yellow line.\n"


@pytest.fixture(scope="module")
def model_name() -> str:
    return f"test_{uuid.uuid4().hex[:10]}"


def _settings(database: Database, storage: Path, model_name: str) -> Settings:
    return Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        app_env="test",
        database_url=database.app_url,
        valkey_url=VALKEY_URL or "",
        qdrant_url=QDRANT_URL or "",
        authz_outbox_worker=False,
        ingestion_mode="inline",
        embedding_provider="hash",
        retrieval_min_similarity=0.0,
        embedding_model=model_name,
        embedding_dimensions=64,
        object_storage_path=str(storage),
    )


def _licensed() -> ExtensionRegistry:
    pytest.importorskip("workmind_ee", reason="install the ee extra: uv sync --extra ee")
    now = datetime.now(UTC)
    license_ = License(
        "Acme Robotics",
        frozenset(FEATURES),
        now - timedelta(days=1),
        now + timedelta(days=30),
        key_id="test",
    )
    return enable_extensions(Edition(license_))


@pytest.fixture
def licensed(database: Database, tmp_path: Path, model_name: str) -> Iterator[TestClient]:
    app = create_app(_settings(database, tmp_path, model_name), extensions=_licensed())
    with TestClient(app) as client:
        yield client


@pytest.fixture
def community(database: Database, tmp_path: Path, model_name: str) -> Iterator[TestClient]:
    app = create_app(
        _settings(database, tmp_path, model_name),
        extensions=ExtensionRegistry(Edition.community()),
    )
    with TestClient(app) as client:
        yield client


@pytest.fixture(scope="module", autouse=True)
def drop_test_collection(model_name: str) -> Iterator[None]:
    yield

    async def drop() -> None:
        async with httpx.AsyncClient() as http:
            client = QdrantClient(http, QDRANT_URL or "")
            name = f"chunks_{model_name}_64"
            if await client.collection_exists(name):
                await client.delete_collection(name)

    asyncio.run(drop())


# --- Data ----------------------------------------------------------------------------------------


async def _collection(sessions: SessionFactory, acme: Acme, key: str) -> uuid.UUID:
    async with tenant_transaction(sessions, acme.tenant_id) as session:
        return (await session.scalars(select(Collection.id).where(Collection.key == key))).one()


async def _document(
    sessions: SessionFactory,
    acme: Acme,
    key: str,
    title: str,
    *,
    age: timedelta = OLD,
    trust: str = "reference",
    reviewed: timedelta | None = None,
    archived: bool = False,
    source_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """A document whose only version was uploaded `age` ago."""
    now = datetime.now(UTC)
    collection = await _collection(sessions, acme, key)
    async with tenant_transaction(sessions, acme.tenant_id) as session:
        document = Document(
            tenant_id=acme.tenant_id,
            collection_id=collection,
            title=title,
            filename=f"{uuid.uuid4().hex[:8]}.md",
            mime_type="text/markdown",
            trust=trust,
            sensitivity="internal",
            status="ready",
            owner_id=acme.users["alice"],
            created_at=now - age,
            reviewed_at=now - reviewed if reviewed else None,
            archived_at=now - timedelta(days=1) if archived else None,
            source_id=source_id,
            external_id=f"item-{uuid.uuid4().hex[:6]}" if source_id else None,
        )
        session.add(document)
        await session.flush()
        session.add(
            DocumentVersion(
                tenant_id=acme.tenant_id,
                document_id=document.id,
                version_no=1,
                content_hash=uuid.uuid4().hex * 2,
                storage_key=f"tenants/{acme.tenant_id}/objects/{uuid.uuid4().hex}",
                size_bytes=10,
                created_at=now - age,
            )
        )
        return document.id


async def _audit(sessions: SessionFactory, acme: Acme, action: str) -> list[AuditEvent]:
    async with tenant_transaction(sessions, acme.tenant_id) as session:
        rows = await session.scalars(
            select(AuditEvent).where(AuditEvent.action == action).order_by(AuditEvent.seq)
        )
        return list(rows.all())


async def _row(sessions: SessionFactory, acme: Acme, document_id: uuid.UUID) -> Document | None:
    async with tenant_transaction(sessions, acme.tenant_id) as session:
        return await session.get(Document, document_id)


# --- Without a license ---------------------------------------------------------------------------


async def test_without_a_license_the_document_rules_answer_402(
    community: TestClient, acme: Acme
) -> None:
    for method, path, body in ROUTES:
        response = community.request(method, path, json=body, headers=acme.as_("erin", write=True))
        assert community.request(method, path, json=body).status_code == 401, path
        assert response.status_code == 402, (method, path)
        assert "'retention_policies'" in response.json()["detail"]


# --- Rules ---------------------------------------------------------------------------------------


async def test_admins_set_rules_of_at_least_30_days_for_their_collections(
    licensed: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    url = "/api/v1/admin/retention/documents"
    erin = acme.as_("erin", write=True)
    engineering = str(await _collection(app_sessions, acme, "engineering-docs"))
    sops = str(await _collection(app_sessions, acme, "operations-sops"))

    def rule(collection: str, days: int = 90, action: str = "archive") -> dict[str, Any]:
        return {"collection_id": collection, "days": days, "action": action}

    member = licensed.put(
        url, json={"rules": [rule(engineering)]}, headers=acme.as_("bob", write=True)
    )
    too_short = licensed.put(url, json={"rules": [rule(engineering, 29)]}, headers=erin)
    unknown = licensed.put(url, json={"rules": [rule(str(uuid.uuid4()))]}, headers=erin)
    twice = licensed.put(url, json={"rules": [rule(engineering), rule(engineering)]}, headers=erin)
    bad_action = licensed.put(url, json={"rules": [rule(engineering, 90, "shred")]}, headers=erin)
    saved = licensed.put(
        url, json={"rules": [rule(sops, 365, "delete"), rule(engineering)]}, headers=erin
    )
    listed = licensed.get(url, headers=acme.as_("erin"))
    cleared = licensed.put(url, json={"rules": [rule(sops, 365, "delete")]}, headers=erin)

    assert member.status_code == 403
    assert [too_short.status_code, twice.status_code, bad_action.status_code] == [422, 422, 422]
    assert unknown.status_code == 422
    assert unknown.json()["code"] == "unknown_collection"
    assert saved.status_code == 200, saved.text
    assert [(r["collection"], r["days"], r["action"]) for r in listed.json()["rules"]] == [
        ("Engineering Docs", 90, "archive"),
        ("Operations SOPs", 365, "delete"),
    ]
    assert listed.json()["minimum_days"] == 30
    assert listed.json()["rules"][0]["updated_by"]["display_name"] == "Erin Takahashi"
    assert [r["collection"] for r in cleared.json()["rules"]] == ["Operations SOPs"]
    events = await _audit(app_sessions, acme, "admin.retention.documents_updated")
    assert [len(e.details["after"]) for e in events] == [2, 1]
    assert events[1].details["before"] != []


async def test_the_preview_is_exact_and_a_run_does_exactly_that(
    licensed: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    eng, sops = "engineering-docs", "operations-sops"
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        source = Source(
            tenant_id=acme.tenant_id,
            key="wiki",
            name="Wiki",
            kind="web",
            collection_id=await _collection(app_sessions, acme, eng),
            owner_id=acme.users["erin"],
            config={"start_urls": ["https://wiki.example/"]},
            enabled=False,
        )
        session.add(source)
        await session.flush()
        source_id = source.id
    # Engineering Docs: delete what has not been updated for 30 days.
    stale = await _document(app_sessions, acme, eng, "Old runbook")
    await _document(app_sessions, acme, eng, "New runbook", age=RECENT)
    await _document(app_sessions, acme, eng, "Reviewed runbook", reviewed=RECENT)
    await _document(app_sessions, acme, eng, "Official standard", trust="official")
    pending = await _document(app_sessions, acme, eng, "Standard to be approved")
    held = await _document(app_sessions, acme, eng, "Incident report")
    synced = await _document(app_sessions, acme, eng, "Wiki page", source_id=source_id)
    transcript = await _document(app_sessions, acme, eng, "Design review transcript")
    # Operations SOPs: archive instead.
    to_archive = await _document(app_sessions, acme, sops, "Old dock rota")
    synced_archive = await _document(app_sessions, acme, sops, "Synced rota", source_id=source_id)
    await _document(app_sessions, acme, sops, "Archived rota", archived=True)
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        session.add(
            Approval(
                tenant_id=acme.tenant_id,
                tool=OFFICIAL_KIND,
                arguments={"document_id": str(pending)},
                summary="Make it official",
                requested_by=acme.users["marcus.bell"],
                approver_id=acme.users["erin"],
                expires_at=datetime.now(UTC) + timedelta(days=14),
                origin={"kind": "official_status", "document_id": str(pending)},
            )
        )
        session.add(
            LegalHold(
                tenant_id=acme.tenant_id,
                matter="Outage review",
                reason="Keep",
                target_type="document",
                target_id=held,
            )
        )
        session.add(
            Meeting(
                tenant_id=acme.tenant_id,
                title="Design review",
                collection_id=await _collection(app_sessions, acme, eng),
                owner_id=acme.users["bob"],
                filename="review.vtt",
                media_key=f"tenants/{acme.tenant_id}/objects/{uuid.uuid4().hex}",
                status="ready",
                document_id=transcript,
            )
        )
    erin = acme.as_("erin", write=True)
    url = "/api/v1/admin/retention/documents"
    saved = licensed.put(
        url,
        json={
            "rules": [
                {
                    "collection_id": str(await _collection(app_sessions, acme, eng)),
                    "days": 30,
                    "action": "delete",
                },
                {
                    "collection_id": str(await _collection(app_sessions, acme, sops)),
                    "days": 30,
                    "action": "archive",
                },
            ]
        },
        headers=erin,
    )
    assert saved.status_code == 200, saved.text

    preview = licensed.post(f"{url}/preview", headers=erin)
    run = licensed.post(f"{url}/run", json={"confirm": "delete"}, headers=erin)
    unconfirmed = licensed.post(f"{url}/run", json={}, headers=erin)
    again = licensed.post(f"{url}/preview", headers=erin)

    assert preview.status_code == 200, preview.text
    by_collection = {c["collection"]: c for c in preview.json()["collections"]}
    assert by_collection["Engineering Docs"]["count"] == 1
    assert by_collection["Engineering Docs"]["kept"] == {
        "legal_hold": 1,
        "official": 2,  # official, and waiting for official approval
        "meeting_transcript": 1,
        "synced": 1,
    }
    assert by_collection["Operations SOPs"]["count"] == 2  # synced documents can be archived
    assert by_collection["Operations SOPs"]["kept"] == dict.fromkeys(
        ("legal_hold", "official", "meeting_transcript", "synced"), 0
    )
    assert (preview.json()["would_delete"], preview.json()["would_archive"]) == (1, 2)
    assert run.status_code == 200, run.text
    assert (run.json()["deleted"], run.json()["archived"]) == (1, 2)  # what the preview said
    assert run.json()["kept"] == preview.json()["kept"]
    assert unconfirmed.status_code == 422
    assert (again.json()["would_delete"], again.json()["would_archive"]) == (0, 0)
    assert await _row(app_sessions, acme, stale) is None
    for kept in (pending, held, synced, transcript):
        assert await _row(app_sessions, acme, kept) is not None
    for archived_id in (to_archive, synced_archive):
        archived = await _row(app_sessions, acme, archived_id)
        assert archived is not None
        assert archived.archived_at is not None
    deleted_events = await _audit(app_sessions, acme, "knowledge.document.deleted")
    assert [(e.resource_id, e.actor_id, e.details["reason"]) for e in deleted_events] == [
        (str(stale), acme.users["erin"], "retention")
    ]
    archived_events = await _audit(app_sessions, acme, "knowledge.document.archived")
    assert sorted(e.resource_id or "" for e in archived_events) == sorted(
        [str(to_archive), str(synced_archive)]
    )
    (run_event,) = await _audit(app_sessions, acme, "admin.retention.documents_run")
    assert (run_event.details["deleted"], run_event.details["archived"]) == (1, 2)
    assert run_event.details["trigger"] == "manual"
    listed = licensed.get(url, headers=acme.as_("erin")).json()["rules"]
    assert {r["collection"]: r["last_run"]["count"] for r in listed} == {
        "Engineering Docs": 1,
        "Operations SOPs": 2,
    }


async def test_a_draft_is_previewed_without_saving_it_and_the_daily_task_applies_the_rules(
    licensed: TestClient, acme: Acme, app_sessions: SessionFactory, tmp_path: Path
) -> None:
    from workmind_ee import document_retention  # the licensed fixture skipped without the package

    eng = await _collection(app_sessions, acme, "engineering-docs")
    stale = await _document(app_sessions, acme, "engineering-docs", "Old design note")
    draft = licensed.post(
        "/api/v1/admin/retention/documents/preview",
        json={"rules": [{"collection_id": str(eng), "days": 30, "action": "archive"}]},
        headers=acme.as_("erin", write=True),
    )
    nothing_saved = licensed.get("/api/v1/admin/retention/documents", headers=acme.as_("erin"))
    async with httpx.AsyncClient() as http:
        qdrant = QdrantClient(http, QDRANT_URL or "")
        storage = LocalObjectStorage(tmp_path)
        without_rules, had_none = await document_retention.apply_rules(
            app_sessions,
            storage,
            qdrant,
            "missing",
            acme.tenant_id,
            actor=Actor.system(),
            trigger="schedule",
        )
        licensed.put(
            "/api/v1/admin/retention/documents",
            json={"rules": [{"collection_id": str(eng), "days": 30, "action": "archive"}]},
            headers=acme.as_("erin", write=True),
        )
        daily, had_rules = await document_retention.apply_rules(
            app_sessions,
            storage,
            qdrant,
            "missing",
            acme.tenant_id,
            actor=Actor.system(),
            trigger="schedule",
        )

    assert draft.json()["would_archive"] == 1
    assert nothing_saved.json()["rules"] == []
    assert (had_none, without_rules.archived) == (False, 0)
    assert (had_rules, daily.archived) == (True, 1)
    archived = await _row(app_sessions, acme, stale)
    assert archived is not None
    assert archived.archived_at is not None
    (event,) = await _audit(app_sessions, acme, "admin.retention.documents_run")
    assert (event.actor_type, event.details["trigger"]) == ("system", "schedule")


# --- Archived documents and search ------------------------------------------------------------


def _upload(api: TestClient, acme: Acme, collection_id: uuid.UUID, name: str, data: bytes) -> str:
    uploaded = api.post(
        f"/api/v1/collections/{collection_id}/documents",
        params={"filename": name},
        content=data,
        headers={**acme.as_("alice", write=True), "Content-Type": "text/markdown"},
    )
    assert uploaded.status_code == 202, uploaded.text
    document_id: str = uploaded.json()["document"]["id"]
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        body = api.get(f"/api/v1/documents/{document_id}", headers=acme.as_("alice")).json()
        if body["status"] in ("ready", "failed"):
            assert body["status"] == "ready", body
            return document_id
        time.sleep(0.2)
    raise AssertionError("ingestion did not finish")


async def _active_points(model_name: str, acme: Acme, document_id: str) -> list[bool]:
    async with httpx.AsyncClient() as http:
        response = await http.post(
            f"{QDRANT_URL}/collections/chunks_{model_name}_64/points/scroll",
            json={
                "filter": {
                    "must": [
                        match("tenant_id", str(acme.tenant_id)),
                        match("document_id", document_id),
                    ]
                },
                "limit": 100,
                "with_payload": True,
            },
        )
    return [bool(p["payload"]["is_active"]) for p in response.json()["result"]["points"]]


async def test_an_archived_document_leaves_search_until_restored_or_updated(
    licensed: TestClient, acme: Acme, app_sessions: SessionFactory, model_name: str
) -> None:
    guidance = await _collection(app_sessions, acme, "hr-guidance")  # Alice edits it
    restored_id = _upload(licensed, acme, guidance, "dock-safety.md", NOTE)
    updated_id = _upload(licensed, acme, guidance, "rota.md", b"# Rota\n\nMonday: Ivan.\n")
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        await session.execute(
            update(DocumentVersion)
            .where(DocumentVersion.document_id.in_([uuid.UUID(restored_id), uuid.UUID(updated_id)]))
            .values(created_at=datetime.now(UTC) - OLD)
        )
        await session.execute(
            update(Document)
            .where(Document.id.in_([uuid.UUID(restored_id), uuid.UUID(updated_id)]))
            .values(created_at=datetime.now(UTC) - OLD)
        )
    erin = acme.as_("erin", write=True)
    licensed.put(
        "/api/v1/admin/retention/documents",
        json={"rules": [{"collection_id": str(guidance), "days": 30, "action": "archive"}]},
        headers=erin,
    )
    run = licensed.post(
        "/api/v1/admin/retention/documents/run", json={"confirm": "delete"}, headers=erin
    )
    archived = licensed.get(f"/api/v1/documents/{restored_id}", headers=acme.as_("alice")).json()
    before_restore = await _active_points(model_name, acme, restored_id)

    by_viewer = licensed.post(
        f"/api/v1/documents/{restored_id}/unarchive", headers=acme.as_("bob", write=True)
    )
    restored = licensed.post(
        f"/api/v1/documents/{restored_id}/unarchive", headers=acme.as_("alice", write=True)
    )
    twice = licensed.post(
        f"/api/v1/documents/{restored_id}/unarchive", headers=acme.as_("alice", write=True)
    )
    after_restore = await _active_points(model_name, acme, restored_id)
    _upload(licensed, acme, guidance, "rota.md", b"# Rota\n\nMonday: Olivia.\n")  # new content
    after_update = await _active_points(model_name, acme, updated_id)
    preview = licensed.post("/api/v1/admin/retention/documents/preview", headers=erin)

    assert run.json()["archived"] == 2
    assert archived["archived_at"] is not None
    assert before_restore
    assert not any(before_restore)
    assert by_viewer.status_code in (403, 404)
    assert restored.status_code == 200, restored.text
    assert restored.json()["archived_at"] is None
    assert twice.status_code == 409
    assert twice.json()["code"] == "not_archived"
    assert after_restore
    assert all(after_restore)
    assert after_update
    assert all(after_update)
    updated = await _row(app_sessions, acme, uuid.UUID(updated_id))
    assert updated is not None
    assert updated.archived_at is None
    # A restore and new content both count as updates: nothing is due again.
    assert preview.json()["would_archive"] == 0
    (event,) = await _audit(app_sessions, acme, "knowledge.document.unarchived")
    assert (event.resource_id, event.actor_id) == (restored_id, acme.users["alice"])
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        reminded = await session.scalar(
            select(func.count()).select_from(Document).where(Document.archived_at.is_not(None))
        )
    assert reminded == 0
