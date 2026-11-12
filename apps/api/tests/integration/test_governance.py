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
from workmind.modules.identity.models import User
from workmind.modules.identity.service import load_identity
from workmind.modules.knowledge import governance
from workmind.modules.knowledge.models import Collection, CollectionGrant, Document, DocumentVersion
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
    """A document row as an upload leaves it, with one version (no file needed for
    governance)."""
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
        await add_version(session, acme, document.id, 1)
        return document.id


async def add_version(
    session: Any, acme: Acme, document_id: uuid.UUID, version_no: int
) -> DocumentVersion:
    version = DocumentVersion(
        tenant_id=acme.tenant_id,
        document_id=document_id,
        version_no=version_no,
        content_hash=uuid.uuid4().hex * 2,
        storage_key=f"test/{uuid.uuid4().hex}",
        size_bytes=10,
    )
    session.add(version)
    await session.flush()
    return version


async def official_approvals(
    sessions: SessionFactory, acme: Acme, document_id: uuid.UUID
) -> dict[uuid.UUID, Approval]:
    """The copies of the official-status requests for a document, by approver."""
    async with tenant_transaction(sessions, acme.tenant_id) as session:
        rows = await session.scalars(
            select(Approval)
            .where(
                Approval.tool == governance.OFFICIAL_KIND,
                Approval.origin["document_id"].astext == str(document_id),
            )
            .order_by(Approval.created_at)
        )
        return {a.approver_id: a for a in rows}


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
    # The handbook: Alice owns it and edits it, so her request goes to the org admins (who can
    # open it: it is shared with the whole organization).
    document_id = await add_document(
        app_sessions, acme, "company-handbook", "Leave FAQ", owner="tom.becker"
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
    rejections = await audit(app_sessions, acme, "approval.rejected")  # as documented
    assert [e.resource_id for e in rejections] == [str(approval.id)]


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


async def test_new_content_for_an_official_document_needs_a_new_approval(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    """An editor cannot replace an official document's content and keep the label: the new
    version drops to the collection's non-official default and loses the old review."""
    handbook = await collection_id(app_sessions, acme, "company-handbook")  # default: official
    url = f"/api/v1/collections/{handbook}/documents"
    params = {"filename": "expense-probe.md"}
    alice = acme.as_("alice", write=True)
    headers = {**alice, "Content-Type": "text/markdown"}
    first = api.post(url, params=params, content=b"# Expenses\n\nMeals: 30 EUR a day.\n",
                     headers=headers)  # fmt: skip
    document_id = first.json()["document"]["id"]
    wait_ready(api, acme, "alice", document_id)
    reviewed = api.post(
        f"/api/v1/documents/{document_id}/review", json={"note": "v1"}, headers=alice
    )
    same = api.post(url, params=params, content=b"# Expenses\n\nMeals: 30 EUR a day.\n",
                    headers=headers)  # fmt: skip
    replaced = api.post(
        url, params=params, content=b"# Expenses\n\nMeals: 3000 EUR a day.\n", headers=headers
    )
    after = wait_ready(api, acme, "alice", document_id)
    direct = api.patch(
        f"/api/v1/documents/{document_id}", json={"trust": "official"}, headers=alice
    )

    assert first.json()["document"]["trust"] == "official"  # the collection's standing decision
    assert reviewed.json()["reviewed_at"] is not None
    assert same.json()["unchanged"] is True
    assert replaced.status_code == 202, replaced.text
    assert replaced.json()["document"]["id"] == document_id  # a new version, not a new document
    assert replaced.json()["document"]["trust"] == "reference"
    assert after["trust"] == "reference"
    assert after["reviewed_at"] is None
    assert after["reviewed_by"] is None
    assert [v["version_no"] for v in after["versions"]] == [2, 1]
    assert direct.status_code == 403  # back to official only through an approval
    events = await audit(app_sessions, acme, "knowledge.document.demoted")
    assert [(e.resource_id, e.details["reason"], e.details["after"]) for e in events] == [
        (document_id, "new_version", "reference")
    ]


async def test_new_content_voids_a_pending_request_and_an_approval_is_for_one_version(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    """The approver approves the content they could read when deciding, never content swapped
    in while the request was pending."""
    handbook = await collection_id(app_sessions, acme, "company-handbook")
    document_id = await add_document(
        app_sessions, acme, "company-handbook", "Gift policy", owner="alice"
    )
    official = f"/api/v1/documents/{document_id}/official"
    headers = acme.as_("alice", write=True)
    requested = api.post(official, json={"note": "Checked."}, headers=headers)
    copies = await official_approvals(app_sessions, acme, document_id)
    swapped = api.post(
        f"/api/v1/collections/{handbook}/documents",
        params={"filename": "gift-policy.md"},  # the same file: version 2
        content=b"# Gifts\n\nAny gift is fine.\n",
        headers={**headers, "Content-Type": "text/markdown"},
    )
    voided = await official_approvals(app_sessions, acme, document_id)
    late = api.post(
        f"/api/v1/approvals/{copies[acme.users['erin']].id}/decision",
        json={"decision": "approve"},
        headers=acme.as_("erin", write=True),
    )
    again = api.post(official, json={"note": "Checked v2."}, headers=headers)

    assert requested.status_code == 200, requested.text
    assert {a.arguments["version"] for a in copies.values()} == {1}
    assert swapped.status_code == 202, swapped.text
    assert {a.status for a in voided.values()} == {"failed"}
    assert late.status_code == 409  # nothing left to decide
    told = await notifications(app_sessions, acme, "alice", "approval.failed")
    assert [n.title for n in told] == ["Ask again: Make “Gift policy” official"]
    assert again.status_code == 200, again.text  # a new request, for version 2

    # A version stored without the upload path (a race): approving still checks the version.
    pending = [
        a
        for a in (await official_approvals(app_sessions, acme, document_id)).values()
        if a.status == "pending"
    ]
    assert {a.arguments["version"] for a in pending} == {2}
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        await add_version(session, acme, document_id, 3)
    raced = api.post(
        f"/api/v1/approvals/{pending[0].id}/decision",
        json={"decision": "approve"},
        headers=acme.as_("erin" if pending[0].approver_id == acme.users["erin"] else "priya.raman",
                         write=True),
    )  # fmt: skip
    document = await document_row(app_sessions, acme, document_id)

    assert raced.status_code == 200, raced.text
    assert raced.json()["status"] == "failed"
    assert "changed after the request" in raced.json()["result"]["error"]
    assert document.trust == "reference"


async def test_official_requests_go_only_to_people_who_can_open_the_collection(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    # HR guidance: Alice owns it and is its only editor; Erin and Priya administer but cannot
    # open it, so nobody may approve her request until an admin can read it.
    document_id = await add_document(
        app_sessions, acme, "hr-guidance", "Leave FAQ", owner="tom.becker"
    )
    official = f"/api/v1/documents/{document_id}/official"
    nobody = api.post(official, json={"note": "Please."}, headers=acme.as_("alice", write=True))
    guidance = await collection_id(app_sessions, acme, "hr-guidance")
    shared = api.put(
        f"/api/v1/admin/collections/{guidance}/grants",
        json={"viewers": [f"user:{acme.users['erin']}"], "editors": []},
        headers=acme.as_("erin", write=True),
    )
    requested = api.post(official, json={"note": "Please."}, headers=acme.as_("alice", write=True))

    assert nobody.status_code == 409
    assert nobody.json()["code"] == "no_approver"
    assert shared.status_code == 200, shared.text
    assert requested.status_code == 200, requested.text
    assert [a["id"] for a in requested.json()["official_request"]["approvers"]] == [
        str(acme.users["erin"])  # Priya still cannot open it
    ]


async def test_an_approver_who_cannot_read_the_document_sees_no_title_and_cannot_decide(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    """A request made before this rule (or to someone who lost access since) is withheld from
    its approver and cannot be decided by them; the other approver's copy stays valid."""
    document_id = await add_document(
        app_sessions, acme, "hr-guidance", "Secret draft", owner="alice"
    )
    guidance = await collection_id(app_sessions, acme, "hr-guidance")
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        version = (
            await session.scalars(
                select(DocumentVersion).where(DocumentVersion.document_id == document_id)
            )
        ).one()
        # Priya can open HR guidance from now on; Erin cannot.
        session.add(
            CollectionGrant(
                tenant_id=acme.tenant_id,
                collection_id=guidance,
                principal=f"user:{acme.users['priya.raman']}",
                relation="viewer",
            )
        )
        ids = {}
        for approver in ("erin", "priya.raman"):
            approval = Approval(
                tenant_id=acme.tenant_id,
                tool=governance.OFFICIAL_KIND,
                arguments={
                    "document_id": str(document_id),
                    "title": "Secret draft",
                    "collection_id": str(guidance),
                    "collection": "HR Guidance (internal)",
                    "note": "The new bonus scheme.",
                    "version_id": str(version.id),
                    "version": 1,
                },
                summary="Make “Secret draft” official",
                requested_by=acme.users["alice"],
                approver_id=acme.users[approver],
                expires_at=datetime.now(UTC) + timedelta(days=1),
                origin={"kind": "official_status", "request_id": "r1",
                        "document_id": str(document_id), "collection_id": str(guidance)},
            )  # fmt: skip
            session.add(approval)
            await session.flush()
            ids[approver] = approval.id

    listed = api.get("/api/v1/approvals", headers=acme.as_("erin")).json()
    one = api.get(f"/api/v1/approvals/{ids['erin']}", headers=acme.as_("erin")).json()
    by_erin = api.post(
        f"/api/v1/approvals/{ids['erin']}/decision",
        json={"decision": "approve"},
        headers=acme.as_("erin", write=True),
    )
    rejected_by_erin = api.post(
        f"/api/v1/approvals/{ids['erin']}/decision",
        json={"decision": "reject"},
        headers=acme.as_("erin", write=True),
    )
    by_priya = api.post(
        f"/api/v1/approvals/{ids['priya.raman']}/decision",
        json={"decision": "approve"},
        headers=acme.as_("priya.raman", write=True),
    )
    seen_by_requester = api.get(
        f"/api/v1/approvals/{ids['erin']}", headers=acme.as_("alice")
    ).json()

    erins = next(a for a in listed if a["id"] == str(ids["erin"]))
    for shown in (erins, one):
        assert shown["summary"] == "Make a document in “HR Guidance (internal)” official"
        assert "title" not in shown["arguments"]
        assert "note" not in shown["arguments"]
        assert shown["arguments"]["withheld"] is True
    assert by_erin.status_code == 403
    assert by_erin.json()["code"] == "not_allowed"
    assert rejected_by_erin.status_code == 403  # rejecting is checked too
    assert by_priya.status_code == 200, by_priya.text
    assert by_priya.json()["status"] == "executed"  # Erin's refusals used nothing up
    assert seen_by_requester["arguments"]["title"] == "Secret draft"
    assert (await document_row(app_sessions, acme, document_id)).trust == "official"


async def test_a_pending_request_follows_the_collection_owner(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    engineering = await collection_id(app_sessions, acme, "engineering-docs")
    admin_url = f"/api/v1/admin/collections/{engineering}"
    erin = acme.as_("erin", write=True)
    api.patch(admin_url, json={"owner_id": str(acme.users["wei.zhang"])}, headers=erin)
    document_id = await add_document(
        app_sessions, acme, "engineering-docs", "Deploy guide", owner="marcus.bell"
    )
    requested = api.post(
        f"/api/v1/documents/{document_id}/official",
        json={"note": "This is the deploy process."},
        headers=acme.as_("marcus.bell", write=True),
    )
    weis = (await official_approvals(app_sessions, acme, document_id))[acme.users["wei.zhang"]]
    moved = api.patch(admin_url, json={"owner_id": str(acme.users["fatima.hassan"])}, headers=erin)
    copies = await official_approvals(app_sessions, acme, document_id)
    inbox = api.get("/api/v1/approvals", headers=acme.as_("fatima.hassan")).json()
    by_former_owner = api.post(
        f"/api/v1/approvals/{weis.id}/decision",
        json={"decision": "reject"},
        headers=acme.as_("wei.zhang", write=True),
    )
    detail = api.get(f"/api/v1/documents/{document_id}", headers=acme.as_("marcus.bell")).json()
    approved = api.post(
        f"/api/v1/approvals/{copies[acme.users['fatima.hassan']].id}/decision",
        json={"decision": "approve"},
        headers=acme.as_("fatima.hassan", write=True),
    )

    assert requested.status_code == 200, requested.text
    assert moved.status_code == 200, moved.text
    assert copies[acme.users["wei.zhang"]].status == "superseded"
    assert copies[acme.users["fatima.hassan"]].status == "pending"
    assert copies[acme.users["fatima.hassan"]].expires_at == weis.expires_at
    assert [a["summary"] for a in inbox] == ["Make “Deploy guide” official"]
    assert by_former_owner.status_code == 409  # no longer theirs to decide
    assert [a["display_name"] for a in detail["official_request"]["approvers"]] == ["Fatima Hassan"]
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "executed"
    events = await audit(app_sessions, acme, "knowledge.document.official_rerouted")
    assert [(e.details["before"], e.details["after"]) for e in events] == [
        ([str(acme.users["wei.zhang"])], [str(acme.users["fatima.hassan"])])
    ]


async def test_an_access_token_cannot_decide_an_official_status_request(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    document_id = await add_document(
        app_sessions, acme, "company-handbook", "Travel", owner="alice"
    )
    api.post(
        f"/api/v1/documents/{document_id}/official",
        json={"note": "Checked."},
        headers=acme.as_("alice", write=True),
    )
    erins = (await official_approvals(app_sessions, acme, document_id))[acme.users["erin"]]
    token = api.post(
        "/api/v1/me/tokens",
        json={"name": "laptop", "scopes": ["assistant:ask"]},
        headers=acme.as_("erin", write=True),
    ).json()["token"]

    by_token = api.post(
        f"/api/v1/approvals/{erins.id}/decision",
        json={"decision": "approve"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert by_token.status_code == 403
    assert by_token.json()["code"] == "session_required"
    assert (await document_row(app_sessions, acme, document_id)).trust == "reference"


async def test_the_review_reminder_follows_the_document_to_its_new_owner(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    document_id = await add_document(
        app_sessions,
        acme,
        "hr-guidance",
        "Onboarding FAQ",
        owner="alice",
        next_review_at=datetime.now(UTC) - timedelta(days=1),
    )
    first = await governance.send_review_reminders(app_sessions, acme.tenant_id)
    task_id = (await document_row(app_sessions, acme, document_id)).review_task_id
    handed = api.put(
        f"/api/v1/documents/{document_id}/owner",
        json={"owner_id": str(acme.users["tom.becker"])},
        headers=acme.as_("alice", write=True),
    )
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        task = await session.get(Task, task_id)
    told = await notifications(app_sessions, acme, "tom.becker", "document.owner")

    assert first.reminded == [uuid.UUID(str(document_id))]
    assert handed.status_code == 200, handed.text
    assert task is not None
    assert (task.assignee_id, task.status) == (acme.users["tom.becker"], "open")
    assert [n.body for n in told] == [
        "You answer for its content and its reviews. Its review is due: the review task is now "
        "yours."
    ]

    # A cycle whose task was closed without a review: the next run reminds the new owner.
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        await session.execute(update(Task).where(Task.id == task_id).values(status="done"))
    back = api.put(
        f"/api/v1/documents/{document_id}/owner",
        json={"owner_id": str(acme.users["alice"])},
        headers=acme.as_("alice", write=True),
    )
    later = await governance.send_review_reminders(app_sessions, acme.tenant_id)
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        tasks = list(
            await session.scalars(
                select(Task).where(Task.origin["document_id"].astext == str(document_id))
            )
        )
    assert back.status_code == 200, back.text
    assert later.reminded == [uuid.UUID(str(document_id))]
    assert sorted((t.assignee_id == acme.users["alice"], t.status) for t in tasks) == [
        (False, "done"),
        (True, "open"),
    ]


async def test_a_decision_by_someone_no_longer_allowed_changes_nothing(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    document_id = await add_document(
        app_sessions, acme, "company-handbook", "Phones", owner="alice"
    )
    api.post(
        f"/api/v1/documents/{document_id}/official",
        json={"note": "Checked."},
        headers=acme.as_("alice", write=True),
    )
    copies = await official_approvals(app_sessions, acme, document_id)
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        await session.execute(
            update(User).where(User.id == acme.users["erin"]).values(org_role="member")
        )
    try:
        by_former_admin = api.post(
            f"/api/v1/approvals/{copies[acme.users['erin']].id}/decision",
            json={"decision": "approve"},
            headers=acme.as_("erin", write=True),
        )
        after = await official_approvals(app_sessions, acme, document_id)
        by_owner = api.post(
            f"/api/v1/approvals/{copies[acme.users['priya.raman']].id}/decision",
            json={"decision": "approve"},
            headers=acme.as_("priya.raman", write=True),
        )
    finally:
        async with tenant_transaction(app_sessions, acme.tenant_id) as session:
            await session.execute(
                update(User).where(User.id == acme.users["erin"]).values(org_role="admin")
            )

    assert by_former_admin.status_code == 403
    assert {a.status for a in after.values()} == {"pending"}  # nothing was used up
    assert by_owner.status_code == 200, by_owner.text
    assert by_owner.json()["status"] == "executed"


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


async def test_the_overview_withholds_notes_where_it_withholds_titles(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    """Erin administers but cannot open HR guidance: she learns that a document there was
    demoted or waits for approval, not what it is or why."""
    demoted_id = await add_document(
        app_sessions, acme, "hr-guidance", "Bonus draft", owner="alice", trust="official"
    )
    api.patch(
        f"/api/v1/documents/{demoted_id}",
        json={"trust": "reference", "note": "The bonus scheme was cancelled."},
        headers=acme.as_("alice", write=True),
    )
    pending_id = await add_document(app_sessions, acme, "hr-guidance", "Pay rises", owner="alice")
    guidance = await collection_id(app_sessions, acme, "hr-guidance")
    api.patch(  # Tom (who can open it) owns it, so Alice's request goes to him
        f"/api/v1/admin/collections/{guidance}",
        json={"owner_id": str(acme.users["tom.becker"])},
        headers=acme.as_("erin", write=True),
    )
    requested = api.post(
        f"/api/v1/documents/{pending_id}/official",
        json={"note": "Salary rises of 9% from March."},
        headers=acme.as_("alice", write=True),
    )

    admin = api.get("/api/v1/governance/overview", headers=acme.as_("erin")).json()
    owner = api.get("/api/v1/governance/overview", headers=acme.as_("tom.becker")).json()

    assert requested.status_code == 200, requested.text
    assert [(d["title"], d["note"]) for d in admin["recent_demotions"]] == [(None, None)]
    assert [(p["document"]["title"], p["note"]) for p in admin["pending_official"]] == [
        (None, None)
    ]
    assert [(d["title"], d["note"]) for d in owner["recent_demotions"]] == [
        ("Bonus draft", "The bonus scheme was cancelled.")
    ]
    assert [(p["document"]["title"], p["note"]) for p in owner["pending_official"]] == [
        ("Pay rises", "Salary rises of 9% from March.")
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
