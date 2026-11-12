"""Knowledge governance over real HTTP (docs/governance.md): owners, review reminders, the
official approval flow and the governance overview.

Needs the database, TEST_VALKEY_URL and TEST_QDRANT_URL (trust changes update the index).
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

from tests.integration.conftest import ACME, VALKEY_URL, Acme, Database, SessionFactory
from workmind.core.config import Settings
from workmind.core.context import RequestContext
from workmind.core.tenancy import tenant_transaction
from workmind.main import create_app
from workmind.modules.governance.models import AuditEvent
from workmind.modules.identity.service import load_identity
from workmind.modules.knowledge import governance
from workmind.modules.knowledge.models import Collection, Document
from workmind.modules.retrieval.qdrant import QdrantClient
from workmind.modules.retrieval.service import Retriever
from workmind.modules.work.models import Approval, Notification, Task
from workmind.providers.factory import create_embeddings
from workmind.seed.company import load_company
from workmind.seed.governance_load import seed_governance

QDRANT_URL = os.environ.get("TEST_QDRANT_URL")
pytestmark = pytest.mark.skipif(
    not (QDRANT_URL and VALKEY_URL), reason="set TEST_QDRANT_URL and TEST_VALKEY_URL"
)
POLICY = b"# Parental Leave\n\n## Duration\nParental leave is 16 weeks at full pay.\n"


@pytest.fixture(scope="module")
def collection_name() -> str:
    return f"test_{uuid.uuid4().hex[:10]}"


@pytest.fixture
def api(database: Database, tmp_path: Path, collection_name: str) -> Iterator[TestClient]:
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        app_env="test",
        database_url=database.app_url,
        valkey_url=VALKEY_URL or "",
        qdrant_url=QDRANT_URL or "",
        authz_outbox_worker=False,
        ingestion_mode="inline",
        embedding_provider="hash",
        retrieval_min_similarity=0.0,
        embedding_model=collection_name,
        embedding_dimensions=64,
        object_storage_path=str(tmp_path),
    )
    with TestClient(create_app(settings)) as client:
        yield client


@pytest.fixture(scope="module", autouse=True)
def drop_test_collection(collection_name: str) -> Iterator[None]:
    yield

    async def drop() -> None:
        async with httpx.AsyncClient() as http:
            client = QdrantClient(http, QDRANT_URL or "")
            name = f"chunks_{collection_name}_64"
            if await client.collection_exists(name):
                await client.delete_collection(name)

    asyncio.run(drop())


# --- Helpers -----------------------------------------------------------------------------------


async def collection_id(sessions: SessionFactory, acme: Acme, key: str) -> uuid.UUID:
    async with tenant_transaction(sessions, acme.tenant_id) as session:
        return (await session.scalars(select(Collection.id).where(Collection.key == key))).one()


async def add_document(
    sessions: SessionFactory,
    acme: Acme,
    key: str,
    title: str,
    *,
    owner: str | None = None,
    trust: str = "reference",
    next_review_at: datetime | None = None,
) -> uuid.UUID:
    """A document row as an upload leaves it (no file needed for governance)."""
    collection = await collection_id(sessions, acme, key)
    async with tenant_transaction(sessions, acme.tenant_id) as session:
        document = Document(
            tenant_id=acme.tenant_id,
            collection_id=collection,
            title=title,
            filename=f"{title.lower().replace(' ', '-')}.md",
            mime_type="text/markdown",
            trust=trust,
            sensitivity="internal",
            status="ready",
            owner_id=acme.users[owner] if owner else None,
            next_review_at=next_review_at,
        )
        session.add(document)
        await session.flush()
        return document.id


async def document_row(sessions: SessionFactory, acme: Acme, document_id: uuid.UUID) -> Document:
    async with tenant_transaction(sessions, acme.tenant_id) as session:
        document = await session.get(Document, document_id)
        assert document is not None
        return document


async def notifications(
    sessions: SessionFactory, acme: Acme, person: str, kind: str
) -> list[Notification]:
    async with tenant_transaction(sessions, acme.tenant_id) as session:
        rows = await session.scalars(
            select(Notification).where(
                Notification.user_id == acme.users[person], Notification.kind == kind
            )
        )
        return list(rows.all())


async def audit(sessions: SessionFactory, acme: Acme, action: str) -> list[AuditEvent]:
    async with tenant_transaction(sessions, acme.tenant_id) as session:
        rows = await session.scalars(
            select(AuditEvent).where(AuditEvent.action == action).order_by(AuditEvent.seq)
        )
        return list(rows.all())


def wait_ready(api: TestClient, acme: Acme, who: str, document_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        body = api.get(f"/api/v1/documents/{document_id}", headers=acme.as_(who)).json()
        if body["status"] in ("ready", "failed"):
            return body
        time.sleep(0.2)
    raise AssertionError("ingestion did not finish")


def close_to(
    value: str | None, expected: datetime, slack: timedelta = timedelta(minutes=2)
) -> bool:
    return value is not None and abs(datetime.fromisoformat(value) - expected) < slack


# --- Owners ------------------------------------------------------------------------------------


async def test_uploads_are_owned_by_the_uploader_and_editors_or_admins_hand_them_over(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    handbook = await collection_id(app_sessions, acme, "company-handbook")
    uploaded = api.post(
        f"/api/v1/collections/{handbook}/documents",
        params={"filename": "parental-leave.md"},
        content=POLICY,
        headers={**acme.as_("alice", write=True), "Content-Type": "text/markdown"},
    )
    assert uploaded.status_code == 202, uploaded.text
    document = uploaded.json()["document"]
    detail = api.get(f"/api/v1/documents/{document['id']}", headers=acme.as_("alice")).json()
    url = f"/api/v1/documents/{document['id']}/owner"

    by_viewer = api.put(
        url, json={"owner_id": str(acme.users["bob"])}, headers=acme.as_("bob", write=True)
    )
    to_a_guest = api.put(
        url, json={"owner_id": str(acme.users["mallory"])}, headers=acme.as_("alice", write=True)
    )
    by_editor = api.put(
        url, json={"owner_id": str(acme.users["tom.becker"])}, headers=acme.as_("alice", write=True)
    )
    by_admin = api.put(
        url, json={"owner_id": str(acme.users["alice"])}, headers=acme.as_("erin", write=True)
    )

    assert detail["owner"]["id"] == str(acme.users["alice"])  # the uploader
    assert detail["can_change_owner"]
    assert detail["can_review"]
    assert by_viewer.status_code == 403
    assert to_a_guest.status_code == 422  # mallory cannot open the handbook
    assert to_a_guest.json()["code"] == "owner_cannot_view"
    assert by_editor.status_code == 200, by_editor.text
    assert by_editor.json()["owner"]["display_name"] == "Tom Becker"
    assert by_admin.status_code == 200, by_admin.text
    assert len(await notifications(app_sessions, acme, "tom.becker", "document.owner")) == 1
    changes = await audit(app_sessions, acme, "knowledge.document.owner_changed")
    assert [(e.actor_id, e.details["after"]) for e in changes] == [
        (acme.users["alice"], str(acme.users["tom.becker"])),
        (acme.users["erin"], str(acme.users["alice"])),
    ]


async def test_without_an_uploader_the_collection_owner_owns_it(
    acme: Acme, app_sessions: SessionFactory
) -> None:
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        handbook = (
            await session.scalars(select(Collection).where(Collection.key == "company-handbook"))
        ).one()
        uploader = await governance.default_owner(session, acme.users["bob"], handbook)
        nobody = await governance.default_owner(session, uuid.uuid4(), handbook)
    assert uploader == acme.users["bob"]
    assert nobody == acme.users["alice"]  # the seeded owner of the handbook


# --- Review reminders --------------------------------------------------------------------------


async def test_reminders_come_once_per_cycle_and_a_review_closes_them(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    guidance = await collection_id(app_sessions, acme, "hr-guidance")
    document_id = await add_document(app_sessions, acme, "hr-guidance", "Onboarding", owner="alice")
    created = (await document_row(app_sessions, acme, document_id)).created_at

    interval = api.patch(
        f"/api/v1/admin/collections/{guidance}",
        json={"review_interval_days": 30},
        headers=acme.as_("erin", write=True),
    )
    rescheduled = await document_row(app_sessions, acme, document_id)
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        await session.execute(
            update(Document)
            .where(Document.id == document_id)
            .values(next_review_at=datetime.now(UTC) - timedelta(days=1))
        )
    first = await governance.send_review_reminders(app_sessions, acme.tenant_id)
    again = await governance.send_review_reminders(app_sessions, acme.tenant_id)
    overdue = api.get(f"/api/v1/documents/{document_id}", headers=acme.as_("alice")).json()
    reminded = await document_row(app_sessions, acme, document_id)

    assert interval.status_code == 200, interval.text
    assert interval.json()["review_interval_days"] == 30
    assert rescheduled.next_review_at == created + timedelta(days=30)
    assert first.reminded == [document_id]
    assert again.reminded == []  # the same cycle is never reminded twice
    assert overdue["review_overdue"] is True
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        task = await session.get(Task, reminded.review_task_id)
    assert task is not None
    assert (task.title, task.assignee_id, task.status) == (
        "Review Onboarding",
        acme.users["alice"],
        "open",
    )
    assert task.due_at is not None
    assert timedelta(days=13) < task.due_at - datetime.now(UTC) <= timedelta(days=14)
    assert task.origin["link"] == f"/knowledge/documents/{document_id}"
    assert len(await notifications(app_sessions, acme, "alice", "document.review_due")) == 1

    # A viewer who is not the owner cannot mark it reviewed; the owner can.
    url = f"/api/v1/documents/{document_id}/review"
    by_viewer = api.post(url, json={}, headers=acme.as_("tom.becker", write=True))
    reviewed = api.post(url, json={"note": "Still correct."}, headers=acme.as_("alice", write=True))
    after = await document_row(app_sessions, acme, document_id)
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        closed = await session.get(Task, task.id)
    later = await governance.send_review_reminders(app_sessions, acme.tenant_id)
    next_cycle = await governance.send_review_reminders(
        app_sessions, acme.tenant_id, now=datetime.now(UTC) + timedelta(days=31)
    )

    assert by_viewer.status_code == 403
    assert reviewed.status_code == 200, reviewed.text
    body = reviewed.json()
    assert body["reviewed_by"]["id"] == str(acme.users["alice"])
    assert body["review_overdue"] is False
    assert close_to(body["next_review_at"], datetime.now(UTC) + timedelta(days=30))
    assert after.review_task_id is None
    assert closed is not None
    assert closed.status == "done"
    assert later.reminded == []
    assert next_cycle.reminded == [document_id]  # the next cycle gets its own reminder
    events = await audit(app_sessions, acme, "knowledge.document.reviewed")
    assert [e.details["note"] for e in events] == ["Still correct."]


async def test_a_document_without_an_owner_is_not_reminded(
    acme: Acme, app_sessions: SessionFactory
) -> None:
    past = datetime.now(UTC) - timedelta(days=2)
    await add_document(app_sessions, acme, "operations-sops", "Dock safety", next_review_at=past)
    report = await governance.send_review_reminders(app_sessions, acme.tenant_id)
    assert report.reminded == []
    assert report.without_owner == 1


async def test_admins_alone_set_review_intervals_and_owners(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    guidance = await collection_id(app_sessions, acme, "hr-guidance")
    url = f"/api/v1/admin/collections/{guidance}"
    by_member = api.patch(
        url, json={"review_interval_days": 30}, headers=acme.as_("alice", write=True)
    )
    too_short = api.patch(
        url, json={"review_interval_days": 0}, headers=acme.as_("erin", write=True)
    )
    unknown = api.patch(
        url, json={"owner_id": str(uuid.uuid4())}, headers=acme.as_("erin", write=True)
    )
    cleared = api.patch(url, json={"owner_id": None}, headers=acme.as_("erin", write=True))

    assert by_member.status_code == 403
    assert too_short.status_code == 422
    assert unknown.status_code == 422
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["owner"] is None
    assert cleared.json()["review_interval_days"] == 180  # left out: as seeded


# --- Official status ---------------------------------------------------------------------------


async def test_official_needs_an_approval_and_nobody_approves_their_own_request(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    # HR guidance: Alice owns it and edits it, so her request goes to the org admins.
    document_id = await add_document(
        app_sessions, acme, "hr-guidance", "Leave FAQ", owner="tom.becker"
    )
    url = f"/api/v1/documents/{document_id}"

    direct = api.patch(url, json={"trust": "official"}, headers=acme.as_("alice", write=True))
    by_viewer = api.post(
        f"{url}/official", json={"note": "Please"}, headers=acme.as_("tom.becker", write=True)
    )
    requested = api.post(
        f"{url}/official",
        json={"note": "Reviewed with legal."},
        headers=acme.as_("alice", write=True),
    )
    twice = api.post(
        f"{url}/official", json={"note": "Again"}, headers=acme.as_("alice", write=True)
    )

    assert direct.status_code == 403
    assert direct.json()["code"] == "official_requires_approval"
    assert by_viewer.status_code == 403
    assert requested.status_code == 200, requested.text
    pending = requested.json()["official_request"]
    assert pending["note"] == "Reviewed with legal."
    assert {a["id"] for a in pending["approvers"]} == {
        str(acme.users["erin"]),
        str(acme.users["priya.raman"]),
    }
    assert requested.json()["trust"] == "reference"
    assert twice.status_code == 409

    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        approvals = {
            a.approver_id: a
            for a in await session.scalars(
                select(Approval).where(Approval.tool == governance.OFFICIAL_KIND)
            )
        }
    assert acme.users["alice"] not in approvals  # never self-approval
    erins = approvals[acme.users["erin"]]
    priyas = approvals[acme.users["priya.raman"]]
    decision = f"/api/v1/approvals/{erins.id}/decision"

    by_requester = api.post(
        decision, json={"decision": "approve"}, headers=acme.as_("alice", write=True)
    )
    edited = api.post(
        decision,
        json={"decision": "approve", "arguments": {"document_id": str(uuid.uuid4())}},
        headers=acme.as_("erin", write=True),
    )
    approved = api.post(
        decision, json={"decision": "approve"}, headers=acme.as_("erin", write=True)
    )
    late = api.post(
        f"/api/v1/approvals/{priyas.id}/decision",
        json={"decision": "reject"},
        headers=acme.as_("priya.raman", write=True),
    )
    document = api.get(url, headers=acme.as_("alice")).json()

    assert by_requester.status_code == 404
    assert edited.status_code == 422
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "executed"
    assert late.status_code == 409  # superseded by Erin's decision
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        superseded = await session.get(Approval, priyas.id)
    assert superseded is not None
    assert superseded.status == "superseded"
    assert document["trust"] == "official"
    assert document["official_request"] is None
    assert document["reviewed_by"]["id"] == str(acme.users["erin"])  # approval is a review
    told = await notifications(app_sessions, acme, "alice", "approval.executed")
    assert [n.title for n in told] == ["Executed: Make “Leave FAQ” official"]
    events = await audit(app_sessions, acme, "knowledge.document.official_approved")
    assert [(e.actor_id, e.resource_id) for e in events] == [(acme.users["erin"], str(document_id))]


async def test_the_collection_owner_decides_and_a_rejection_keeps_the_trust(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    engineering = await collection_id(app_sessions, acme, "engineering-docs")
    owned = api.patch(
        f"/api/v1/admin/collections/{engineering}",
        json={"owner_id": str(acme.users["wei.zhang"])},
        headers=acme.as_("erin", write=True),
    )
    document_id = await add_document(
        app_sessions, acme, "engineering-docs", "Deploy guide", owner="marcus.bell"
    )
    # Marcus edits engineering docs as the department's manager; Wei owns the collection.
    requested = api.post(
        f"/api/v1/documents/{document_id}/official",
        json={"note": "This is the deploy process."},
        headers=acme.as_("marcus.bell", write=True),
    )
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        approval = (
            await session.scalars(
                select(Approval).where(Approval.origin["document_id"].astext == str(document_id))
            )
        ).one()
    rejected = api.post(
        f"/api/v1/approvals/{approval.id}/decision",
        json={"decision": "reject", "note": "Needs the rollback steps first."},
        headers=acme.as_("wei.zhang", write=True),
    )
    document = await document_row(app_sessions, acme, document_id)
    told = await notifications(app_sessions, acme, "marcus.bell", "approval.rejected")

    assert owned.status_code == 200, owned.text
    assert requested.status_code == 200, requested.text
    assert approval.approver_id == acme.users["wei.zhang"]
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"
    assert document.trust == "reference"
    assert [(n.title, n.body) for n in told] == [
        ("Rejected: Make “Deploy guide” official", "Needs the rollback steps first.")
    ]


async def test_demoting_is_immediate_audited_and_tells_the_owner(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    document_id = await add_document(
        app_sessions,
        acme,
        "company-handbook",
        "Old travel policy",
        owner="tom.becker",
        trust="official",
    )
    demoted = api.patch(
        f"/api/v1/documents/{document_id}",
        json={"trust": "reference", "note": "Replaced by the 2026 policy."},
        headers=acme.as_("alice", write=True),
    )
    restored = api.patch(
        f"/api/v1/documents/{document_id}",
        json={"trust": "official"},
        headers=acme.as_("alice", write=True),
    )
    overview = api.get("/api/v1/governance/overview", headers=acme.as_("erin")).json()

    assert demoted.status_code == 200, demoted.text
    assert demoted.json()["trust"] == "reference"
    assert restored.status_code == 403  # going back needs an approval
    events = await audit(app_sessions, acme, "knowledge.document.demoted")
    assert [(e.actor_id, e.details["after"], e.details["note"]) for e in events] == [
        (acme.users["alice"], "reference", "Replaced by the 2026 policy.")
    ]
    told = await notifications(app_sessions, acme, "tom.becker", "document.demoted")
    assert [(n.title, n.body) for n in told] == [
        ("No longer official: Old travel policy", "Replaced by the 2026 policy.")
    ]
    assert [(d["document_id"], d["title"], d["to"]) for d in overview["recent_demotions"]] == [
        (str(document_id), "Old travel policy", "reference")
    ]


# --- Overview ----------------------------------------------------------------------------------


async def test_the_overview_shows_admins_everything_and_owners_their_collections(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    now = datetime.now(UTC)
    handbook = await add_document(
        app_sessions,
        acme,
        "company-handbook",
        "Expenses",
        owner="alice",
        next_review_at=now - timedelta(days=3),
    )
    records = await add_document(
        app_sessions,
        acme,
        "hr-employee-records",
        "Salary review",
        owner="alice",
        next_review_at=now - timedelta(days=1),
    )
    soon = await add_document(
        app_sessions,
        acme,
        "engineering-docs",
        "Runbook",
        owner="bob",
        next_review_at=now + timedelta(days=10),
    )
    orphan = await add_document(app_sessions, acme, "operations-sops", "Forklifts")
    later = await add_document(
        app_sessions,
        acme,
        "company-handbook",
        "Holidays",
        owner="alice",
        next_review_at=now + timedelta(days=90),
    )

    admin = api.get("/api/v1/governance/overview", headers=acme.as_("erin"))
    owner = api.get("/api/v1/governance/overview", headers=acme.as_("alice")).json()
    member = api.get("/api/v1/governance/overview", headers=acme.as_("bob")).json()

    assert admin.status_code == 200, admin.text
    everything = admin.json()
    assert everything["scope"] == "all"
    overdue = {d["id"]: d for d in everything["overdue"]}
    assert set(overdue) == {str(handbook), str(records)}
    assert overdue[str(handbook)]["title"] == "Expenses"
    # Erin administers but cannot open employee records: she sees that it needs a review,
    # not what it is called.
    assert overdue[str(records)]["title"] is None
    assert [d["id"] for d in everything["due_soon"]] == [str(soon)]
    assert str(later) not in {d["id"] for d in everything["due_soon"]}
    assert [d["id"] for d in everything["without_owner"]] == [str(orphan)]

    assert owner["scope"] == "owned"
    assert {c["name"] for c in owner["collections"]} == {
        "Company Handbook",
        "HR Guidance (internal)",
    }
    assert [d["id"] for d in owner["overdue"]] == [str(handbook)]
    assert owner["due_soon"] == []
    assert owner["without_owner"] == []

    assert member == {
        "scope": "owned",
        "collections": [],
        "overdue": [],
        "due_soon": [],
        "without_owner": [],
        "pending_official": [],
        "recent_demotions": [],
    }


async def test_pending_requests_appear_in_the_overview(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    document_id = await add_document(app_sessions, acme, "company-handbook", "Gifts", owner="alice")
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        await session.execute(
            update(Document).where(Document.id == document_id).values(trust="reference")
        )
    api.post(
        f"/api/v1/documents/{document_id}/official",
        json={"note": "Approved by the board."},
        headers=acme.as_("alice", write=True),
    )
    overview = api.get("/api/v1/governance/overview", headers=acme.as_("erin")).json()
    pending = overview["pending_official"]
    assert [(p["document"]["id"], p["requested_by"]["id"], p["note"]) for p in pending] == [
        (str(document_id), str(acme.users["alice"]), "Approved by the board.")
    ]


# --- Sources -----------------------------------------------------------------------------------


async def test_sources_say_when_an_official_document_is_overdue_for_review(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    handbook = await collection_id(app_sessions, acme, "company-handbook")  # default: official
    uploaded = api.post(
        f"/api/v1/collections/{handbook}/documents",
        params={"filename": "leave-overdue.md"},
        content=POLICY,
        headers={**acme.as_("alice", write=True), "Content-Type": "text/markdown"},
    ).json()["document"]
    wait_ready(api, acme, "alice", uploaded["id"])
    document_id = uuid.UUID(uploaded["id"])

    settings: Settings = api.app.state.settings  # pyright: ignore[reportFunctionMemberAccess, reportAttributeAccessIssue]
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        bob = await load_identity(session, acme.users["bob"])
    assert bob is not None
    ctx = RequestContext(
        request_id="t",
        tenant_id=acme.tenant_id,
        user_id=acme.users["bob"],
        org_role="member",
        principals=bob.principals,
        auth_method="session",
        scopes=frozenset({"*"}),
    )

    async def search() -> list[bool]:
        # Lock 1 only (fga=None): this test is about the review flag, not OpenFGA.
        async with httpx.AsyncClient() as http:
            retriever = Retriever(
                qdrant=QdrantClient(http, QDRANT_URL or ""),
                embeddings=create_embeddings(settings, http),
                fga=None,
                sessions=app_sessions,
                collection_name=settings.qdrant_collection,
            )
            result = await retriever.search(ctx, "parental leave weeks", limit=8)
        return [p.review_overdue for p in result.passages if p.document_id == str(document_id)]

    fresh = await search()
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        await session.execute(
            update(Document)
            .where(Document.id == document_id)
            .values(next_review_at=datetime.now(UTC) - timedelta(days=1))
        )
    stale = await search()
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        count = await session.scalar(
            select(func.count()).select_from(Document).where(Document.id == document_id)
        )

    assert count == 1
    assert fresh
    assert not any(fresh)
    assert stale
    assert all(stale)


# --- Seed --------------------------------------------------------------------------------------


async def test_seeded_governance_is_fixed_and_idempotent(
    acme: Acme, owner_sessions: SessionFactory, app_sessions: SessionFactory
) -> None:
    spec = load_company(ACME)
    priya = acme.users["priya.raman"]
    ids: dict[str, uuid.UUID] = {}
    for key, title in (("company-handbook", "Leave policy"), ("engineering-docs", "Runbook")):
        collection = await collection_id(app_sessions, acme, key)
        async with tenant_transaction(app_sessions, acme.tenant_id) as session:
            document = Document(
                tenant_id=acme.tenant_id,
                collection_id=collection,
                title=title,
                filename=f"{key}.md",
                mime_type="text/markdown",
                trust="reference",
                sensitivity="internal",
                source="seed",
                created_by=priya,  # seeding uploads as the org owner
                owner_id=priya,
            )
            session.add(document)
            await session.flush()
            ids[key] = document.id

    first = await seed_governance(owner_sessions, acme.tenant_id, spec)
    second = await seed_governance(owner_sessions, acme.tenant_id, spec)
    handbook = await document_row(app_sessions, acme, ids["company-handbook"])
    runbook = await document_row(app_sessions, acme, ids["engineering-docs"])

    assert (first.owners_set, first.reviews_set) == (2, 2)
    assert (second.owners_set, second.reviews_set) == (0, 0)
    assert handbook.owner_id == acme.users["alice"]
    assert handbook.reviewed_at == datetime(2025, 11, 3, 9, tzinfo=UTC)
    assert handbook.next_review_at == datetime(2026, 11, 3, 9, tzinfo=UTC)  # overdue, Nov 2026
    assert runbook.owner_id == acme.users["marcus.bell"]
    assert runbook.next_review_at == datetime(2026, 11, 28, 9, tzinfo=UTC)  # due within 30 days
