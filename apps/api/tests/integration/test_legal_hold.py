"""Legal holds (Enterprise feature "legal_hold", docs/enterprise.md#legal-hold), in both editions.

Without a license the hold routes answer 402, but holds already in the database are still
enforced: the core refuses every deletion they cover, whatever the license says. With one,
owners and admins place and release holds (audited), auditors read them, and what a hold names
is described without content the reader cannot open. Retention runs keep held items and count
them. Needs the test database, TEST_VALKEY_URL and TEST_QDRANT_URL (deletions clear the index).
"""

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from tests.integration.conftest import VALKEY_URL, Acme, Database, SessionFactory
from workmind.core.config import Settings
from workmind.core.edition import FEATURES, Edition, License
from workmind.core.extensions import ExtensionRegistry, enable_extensions
from workmind.core.tenancy import tenant_transaction
from workmind.main import create_app
from workmind.modules.analytics.models import AnalyticsRun
from workmind.modules.conversations.models import Conversation
from workmind.modules.governance import holds
from workmind.modules.governance.models import AuditEvent, LegalHold
from workmind.modules.knowledge.models import Collection, Document, DocumentVersion
from workmind.modules.meetings.models import Meeting

QDRANT_URL = os.environ.get("TEST_QDRANT_URL")
pytestmark = pytest.mark.skipif(
    not (VALKEY_URL and QDRANT_URL), reason="set TEST_VALKEY_URL and TEST_QDRANT_URL"
)
ROUTES = (
    ("GET", "/api/v1/admin/legal-holds", None),
    (
        "POST",
        "/api/v1/admin/legal-holds",
        {"matter": "M", "reason": "R", "target_type": "user", "target_id": str(uuid.uuid4())},
    ),
    ("POST", f"/api/v1/admin/legal-holds/{uuid.uuid4()}/release", {"reason": "Done"}),
)
OLD = timedelta(days=45)


def _settings(database: Database, storage: Path) -> Settings:
    return Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        app_env="test",
        database_url=database.app_url,
        valkey_url=VALKEY_URL or "",
        qdrant_url=QDRANT_URL or "",
        embedding_model=f"test_{uuid.uuid4().hex[:10]}",
        authz_outbox_worker=False,
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
    registry = enable_extensions(Edition(license_))
    assert {r.feature for r in registry.routers} >= {"legal_hold"}
    return registry


@pytest.fixture
def licensed(database: Database, tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(_settings(database, tmp_path), extensions=_licensed())) as client:
        yield client


@pytest.fixture
def community(database: Database, tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(
        _settings(database, tmp_path), extensions=ExtensionRegistry(Edition.community())
    )
    with TestClient(app) as client:
        yield client


# --- Data ----------------------------------------------------------------------------------------


async def _collection(sessions: SessionFactory, acme: Acme, key: str) -> uuid.UUID:
    async with tenant_transaction(sessions, acme.tenant_id) as session:
        return (await session.scalars(select(Collection.id).where(Collection.key == key))).one()


async def _document(
    sessions: SessionFactory, acme: Acme, key: str, title: str, *, owner: str = "alice"
) -> uuid.UUID:
    collection = await _collection(sessions, acme, key)
    async with tenant_transaction(sessions, acme.tenant_id) as session:
        document = Document(
            tenant_id=acme.tenant_id,
            collection_id=collection,
            title=title,
            filename=f"{uuid.uuid4().hex[:8]}.md",
            mime_type="text/markdown",
            trust="reference",
            sensitivity="internal",
            status="ready",
            owner_id=acme.users[owner],
            created_by=acme.users[owner],
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
            )
        )
        return document.id


async def _meeting(
    sessions: SessionFactory, acme: Acme, *, owner: str, key: str = "operations-sops"
) -> tuple[uuid.UUID, uuid.UUID]:
    """A transcript meeting with its indexed transcript document."""
    transcript = await _document(sessions, acme, key, "Standup transcript", owner=owner)
    collection = await _collection(sessions, acme, key)
    async with tenant_transaction(sessions, acme.tenant_id) as session:
        meeting = Meeting(
            tenant_id=acme.tenant_id,
            title="Standup",
            collection_id=collection,
            owner_id=acme.users[owner],
            filename="standup.vtt",
            media_key=f"tenants/{acme.tenant_id}/objects/{uuid.uuid4().hex}",
            status="ready",
            document_id=transcript,
        )
        session.add(meeting)
        await session.flush()
        return meeting.id, transcript


async def _hold(
    sessions: SessionFactory, acme: Acme, target_type: str, target_id: uuid.UUID
) -> uuid.UUID:
    """A hold as the API would leave it (also how one placed under a license that has since
    lapsed looks)."""
    async with tenant_transaction(sessions, acme.tenant_id) as session:
        hold = LegalHold(
            tenant_id=acme.tenant_id,
            matter="Smith v. Acme",
            reason="Preserve for discovery",
            target_type=target_type,
            target_id=target_id,
            placed_by=acme.users["erin"],
        )
        session.add(hold)
        await session.flush()
        return hold.id


async def _audit(sessions: SessionFactory, acme: Acme, action: str) -> list[AuditEvent]:
    async with tenant_transaction(sessions, acme.tenant_id) as session:
        rows = await session.scalars(
            select(AuditEvent).where(AuditEvent.action == action).order_by(AuditEvent.seq)
        )
        return list(rows.all())


async def _exists(
    sessions: SessionFactory, acme: Acme, model: type[object], id_: uuid.UUID
) -> bool:
    async with tenant_transaction(sessions, acme.tenant_id) as session:
        return await session.get(model, id_) is not None


# --- Without a license ---------------------------------------------------------------------------


async def test_without_a_license_the_hold_routes_answer_402(
    community: TestClient, acme: Acme
) -> None:
    for method, path, body in ROUTES:
        signed_out = community.request(method, path, json=body)
        response = community.request(method, path, json=body, headers=acme.as_("erin", write=True))
        assert signed_out.status_code == 401, path
        assert response.status_code == 402, (method, path)
        assert response.json()["code"] == "enterprise_feature"
        assert "'legal_hold'" in response.json()["detail"]


async def test_holds_are_enforced_in_community_too(
    community: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    """A license that lapses must not release a hold: the core refuses, whatever the edition."""
    document = await _document(app_sessions, acme, "company-handbook", "Expense policy")
    hold = await _hold(app_sessions, acme, "document", document)

    refused = community.delete(
        f"/api/v1/documents/{document}", headers=acme.as_("alice", write=True)
    )
    events = await _audit(app_sessions, acme, holds.BLOCKED)

    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "legal_hold"
    assert "Smith" not in refused.text  # the matter is for administrators, not the requester
    assert await _exists(app_sessions, acme, Document, document)
    assert [(e.resource_type, e.resource_id, e.actor_id) for e in events] == [
        ("document", str(document), acme.users["alice"])
    ]
    assert events[0].details == {
        "via": "user",
        "holds": [str(hold)],
        "matters": ["Smith v. Acme"],
    }


# --- Every deletion path a person can take --------------------------------------------------------


async def test_held_documents_conversations_and_meetings_cannot_be_deleted(
    licensed: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    alice = acme.as_("alice", write=True)
    in_collection = await _document(app_sessions, acme, "hr-guidance", "Grievance procedure")
    meeting, transcript = await _meeting(app_sessions, acme, owner="olivia.grant")
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        conversation = Conversation(tenant_id=acme.tenant_id, user_id=acme.users["bob"], title="Q")
        session.add(conversation)
        await session.flush()
        conversation_id = conversation.id
    await _hold(
        app_sessions, acme, "collection", await _collection(app_sessions, acme, "hr-guidance")
    )
    await _hold(app_sessions, acme, "user", acme.users["bob"])
    await _hold(app_sessions, acme, "meeting", meeting)

    document = licensed.delete(f"/api/v1/documents/{in_collection}", headers=alice)
    chat = licensed.delete(
        f"/api/v1/conversations/{conversation_id}", headers=acme.as_("bob", write=True)
    )
    olivia = acme.as_("olivia.grant", write=True)
    by_meeting = licensed.delete(f"/api/v1/meetings/{meeting}", headers=olivia)
    by_transcript = licensed.delete(f"/api/v1/documents/{transcript}", headers=olivia)

    for response in (document, chat, by_meeting, by_transcript):
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "legal_hold"
    assert "conversation is under a legal hold" in chat.json()["title"]
    assert await _exists(app_sessions, acme, Document, in_collection)
    assert await _exists(app_sessions, acme, Conversation, conversation_id)
    assert await _exists(app_sessions, acme, Meeting, meeting)
    assert await _exists(app_sessions, acme, Document, transcript)
    events = await _audit(app_sessions, acme, holds.BLOCKED)
    assert [(e.resource_type, e.details["via"]) for e in events] == [
        ("document", "user"),
        ("conversation", "user"),
        ("meeting", "user"),
        ("document", "user"),  # the transcript: its meeting is held
    ]

    # Another of Bob's conversations is held too (the hold names the person); Alice's is not.
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        free = Conversation(tenant_id=acme.tenant_id, user_id=acme.users["alice"], title="Q")
        session.add(free)
        await session.flush()
        free_id = free.id
    assert licensed.delete(f"/api/v1/conversations/{free_id}", headers=alice).status_code == 204


async def test_a_source_with_held_documents_is_not_deleted(
    licensed: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    erin = acme.as_("erin", write=True)
    created = licensed.post(
        "/api/v1/admin/sources",
        json={
            "key": "intranet",
            "name": "Intranet",
            "kind": "github",
            "collection": "company-handbook",
            "config": {"repo": "acme/intranet"},
            "enabled": False,
        },
        headers=erin,
    )
    assert created.status_code == 201, created.text
    synced = await _document(app_sessions, acme, "company-handbook", "Synced page")
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        document = await session.get(Document, synced)
        assert document is not None
        document.source_id, document.external_id = uuid.UUID(created.json()["id"]), "page-1"
    await _hold(app_sessions, acme, "document", synced)

    refused = licensed.delete("/api/v1/admin/sources/intranet", headers=erin)
    still = licensed.get("/api/v1/admin/sources", headers=acme.as_("erin")).json()
    keys = [s["key"] for s in still]

    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "legal_hold"
    assert "intranet" in keys
    assert await _exists(app_sessions, acme, Document, synced)
    (event,) = await _audit(app_sessions, acme, holds.BLOCKED)
    assert (event.resource_id, event.details["source"]) == (str(synced), "intranet")


# --- Placing, listing and releasing (licensed) ---------------------------------------------------


async def test_admins_place_and_release_holds_and_every_step_is_audited(
    licensed: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    erin = acme.as_("erin", write=True)
    meeting, _ = await _meeting(app_sessions, acme, owner="bob", key="engineering-docs")
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        session.add(Conversation(tenant_id=acme.tenant_id, user_id=acme.users["bob"], title="Q"))
        session.add(AnalyticsRun(tenant_id=acme.tenant_id, user_id=acme.users["bob"], question="?"))
    body = {
        "matter": "  Smith v. Acme ",
        "reason": "Preserve Bob's records for discovery",
        "target_type": "user",
        "target_id": str(acme.users["bob"]),
    }

    by_member = licensed.post(
        "/api/v1/admin/legal-holds", json=body, headers=acme.as_("bob", write=True)
    )
    by_auditor = licensed.post(
        "/api/v1/admin/legal-holds", json=body, headers=acme.as_("amara.diallo", write=True)
    )
    unknown = licensed.post(
        "/api/v1/admin/legal-holds", json={**body, "target_id": str(uuid.uuid4())}, headers=erin
    )
    blank = licensed.post("/api/v1/admin/legal-holds", json={**body, "matter": "  "}, headers=erin)
    bad_kind = licensed.post(
        "/api/v1/admin/legal-holds", json={**body, "target_type": "tenant"}, headers=erin
    )
    placed = licensed.post("/api/v1/admin/legal-holds", json=body, headers=erin)
    again = licensed.post(
        "/api/v1/admin/legal-holds", json={**body, "matter": "smith v. acme"}, headers=erin
    )
    listed = licensed.get("/api/v1/admin/legal-holds", headers=acme.as_("amara.diallo"))

    assert [r.status_code for r in (by_member, by_auditor)] == [403, 403]
    assert unknown.status_code == 422
    assert unknown.json()["code"] == "unknown_target"
    assert [blank.status_code, bad_kind.status_code] == [422, 422]
    assert placed.status_code == 201, placed.text
    hold = placed.json()
    assert (hold["matter"], hold["active"], hold["placed_by"]["display_name"]) == (
        "Smith v. Acme",
        True,
        "Erin Takahashi",
    )
    assert hold["target"] == {
        "type": "user",
        "id": str(acme.users["bob"]),
        "label": "Bob Okafor (bob@acme.test)",
        "exists": True,
    }
    assert hold["covers"] == {
        "documents": 1,  # the transcript of his meeting
        "conversations": 1,
        "meetings": 1,
        "analytics_runs": 1,
    }
    assert again.status_code == 409
    assert again.json()["code"] == "already_held"
    assert listed.status_code == 200, listed.text  # auditors read holds
    assert [h["id"] for h in listed.json()["items"]] == [hold["id"]]

    url = f"/api/v1/admin/legal-holds/{hold['id']}/release"
    no_reason = licensed.post(url, json={"reason": ""}, headers=erin)
    released = licensed.post(url, json={"reason": "Case settled"}, headers=erin)
    twice = licensed.post(url, json={"reason": "Again"}, headers=erin)
    missing = licensed.post(
        f"/api/v1/admin/legal-holds/{uuid.uuid4()}/release", json={"reason": "x"}, headers=erin
    )
    active = licensed.get("/api/v1/admin/legal-holds", headers=acme.as_("erin")).json()["items"]
    history = licensed.get(
        "/api/v1/admin/legal-holds", params={"status": "released"}, headers=acme.as_("erin")
    ).json()["items"]
    deleted = licensed.delete(f"/api/v1/meetings/{meeting}", headers=acme.as_("bob", write=True))

    assert no_reason.status_code == 422
    assert released.status_code == 200, released.text
    assert (released.json()["active"], released.json()["release_reason"]) == (False, "Case settled")
    assert released.json()["released_by"]["display_name"] == "Erin Takahashi"
    assert twice.status_code == 409
    assert missing.status_code == 404
    assert active == []
    assert [h["id"] for h in history] == [hold["id"]]
    assert deleted.status_code == 204, deleted.text  # nothing holds it any more
    placed_events = await _audit(app_sessions, acme, "legal_hold.placed")
    released_events = await _audit(app_sessions, acme, "legal_hold.released")
    assert [(e.actor_id, e.details["matter"], e.details["target_type"]) for e in placed_events] == [
        (acme.users["erin"], "Smith v. Acme", "user")
    ]
    assert [(e.actor_id, e.details["reason"]) for e in released_events] == [
        (acme.users["erin"], "Case settled")
    ]


async def test_what_a_hold_names_is_described_without_content_the_reader_cannot_open(
    licensed: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    secret = await _document(app_sessions, acme, "hr-guidance", "Dismissal of J. Doe")
    open_doc = await _document(app_sessions, acme, "company-handbook", "Travel policy")
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        run = AnalyticsRun(
            tenant_id=acme.tenant_id, user_id=acme.users["carol"], question="Bonus of J. Doe?"
        )
        session.add(run)
        await session.flush()
        run_id = run.id
    erin = acme.as_("erin", write=True)
    for kind, target in (("document", secret), ("document", open_doc), ("analytics_run", run_id)):
        response = licensed.post(
            "/api/v1/admin/legal-holds",
            json={
                "matter": "Doe",
                "reason": "HR case",
                "target_type": kind,
                "target_id": str(target),
            },
            headers=erin,
        )
        assert response.status_code == 201, response.text

    listed = licensed.get("/api/v1/admin/legal-holds", headers=acme.as_("erin"))
    labels = {h["target"]["id"]: h["target"]["label"] for h in listed.json()["items"]}

    # Erin cannot open HR Guidance: she learns where the document is, not what it is.
    assert labels[str(secret)] == "A document in “HR Guidance (internal)” you can't open"
    assert labels[str(open_doc)] == "Travel policy (Company Handbook)"
    assert labels[str(run_id)].startswith("An Insights question by Carol Lindqvist, ")
    assert "J. Doe" not in listed.text


# --- Retention (licensed) ------------------------------------------------------------------------


async def test_retention_keeps_held_items_and_says_so(
    licensed: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    now = datetime.now(UTC)
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        for person in ("bob", "alice"):
            session.add(
                Conversation(
                    tenant_id=acme.tenant_id,
                    user_id=acme.users[person],
                    title="Old",
                    last_message_at=now - OLD,
                )
            )
        held_run = AnalyticsRun(
            tenant_id=acme.tenant_id,
            user_id=acme.users["carol"],
            question="?",
            created_at=now - OLD,
        )
        free_run = AnalyticsRun(
            tenant_id=acme.tenant_id,
            user_id=acme.users["carol"],
            question="?",
            created_at=now - OLD,
        )
        session.add_all([held_run, free_run])
        await session.flush()
        held_run_id = held_run.id
        collection = (
            await session.scalars(select(Collection.id).where(Collection.key == "operations-sops"))
        ).one()
        recording = Meeting(
            tenant_id=acme.tenant_id,
            title="Recorded",
            collection_id=collection,
            owner_id=acme.users["olivia.grant"],
            filename="sync.mp3",
            media_key=f"tenants/{acme.tenant_id}/objects/{uuid.uuid4().hex}",
            status="ready",
            created_at=now - OLD,
        )
        session.add(recording)
    await _hold(app_sessions, acme, "user", acme.users["bob"])
    await _hold(app_sessions, acme, "analytics_run", held_run_id)
    await _hold(app_sessions, acme, "collection", collection)
    erin = acme.as_("erin", write=True)
    saved = licensed.put(
        "/api/v1/admin/retention",
        json={"conversations_days": 30, "analytics_runs_days": 30, "meeting_recordings_days": 30},
        headers=erin,
    )
    assert saved.status_code == 200, saved.text

    preview = licensed.post("/api/v1/admin/retention/preview", headers=erin)
    run = licensed.post("/api/v1/admin/retention/run", json={"confirm": "delete"}, headers=erin)
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        conversations = await session.scalar(select(func.count()).select_from(Conversation))
        runs = await session.scalar(select(func.count()).select_from(AnalyticsRun))
        kept_recording = await session.get(Meeting, recording.id)
    (event,) = await _audit(app_sessions, acme, "admin.retention.run")

    expected_deleted = {
        "conversations": 1,
        "analytics_runs": 1,
        "meeting_recordings": 0,
        "read_notifications": 0,
    }
    expected_kept = {
        "conversations": 1,
        "analytics_runs": 1,
        "meeting_recordings": 1,
        "read_notifications": 0,
    }
    assert preview.json()["would_delete"] == expected_deleted
    assert preview.json()["kept_under_hold"] == expected_kept
    assert run.json()["deleted"] == expected_deleted  # exactly what the preview said
    assert run.json()["kept_under_hold"] == expected_kept
    assert (conversations, runs) == (1, 1)
    assert kept_recording is not None
    assert kept_recording.media_deleted_at is None
    assert event.details["kept_under_hold"] == expected_kept


# --- One item and many agree ----------------------------------------------------------------------


async def test_the_single_item_check_and_the_bulk_clauses_agree(
    acme: Acme, app_sessions: SessionFactory
) -> None:
    meeting, transcript = await _meeting(app_sessions, acme, owner="olivia.grant")
    other_meeting, other_transcript = await _meeting(app_sessions, acme, owner="bob")
    loose = await _document(app_sessions, acme, "company-handbook", "Loose")
    await _hold(app_sessions, acme, "document", other_transcript)  # holds its meeting too
    await _hold(app_sessions, acme, "meeting", meeting)  # holds its transcript too

    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        held_documents = set(
            (await session.scalars(select(Document.id).where(holds.document_held()))).all()
        )
        held_meetings = set(
            (await session.scalars(select(Meeting.id).where(holds.meeting_held()))).all()
        )
        one_by_one = {
            d: bool(await holds.covering(session, "document", await session.get(Document, d)))
            for d in (transcript, other_transcript, loose)
        }
        meetings_one_by_one = {
            m: bool(await holds.covering(session, "meeting", await session.get(Meeting, m)))
            for m in (meeting, other_meeting)
        }

    assert held_documents == {transcript, other_transcript}
    assert held_meetings == {meeting, other_meeting}
    assert one_by_one == {transcript: True, other_transcript: True, loose: False}
    assert meetings_one_by_one == {meeting: True, other_meeting: True}
