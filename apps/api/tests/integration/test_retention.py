"""Retention policies (Enterprise, docs/enterprise.md), with and without a license.

Without one, the routes answer 402 from the core's stand-ins. With one, the workmind_ee package
is loaded through its real entry point; preview counts must equal what a run deletes, a run
touches only its own tenant's expired rows and files, and nothing younger than the policy (at
least 30 days) goes. Needs the test database and TEST_VALKEY_URL.
"""

import hashlib
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from tests.integration.conftest import VALKEY_URL, Acme, Database, SessionFactory, seed_acme
from workmind.core.config import Settings
from workmind.core.edition import FEATURES, Edition, License
from workmind.core.extensions import ExtensionRegistry, enable_extensions
from workmind.core.tenancy import tenant_transaction
from workmind.main import create_app
from workmind.modules.analytics.models import AnalyticsRun
from workmind.modules.conversations.models import Conversation, Message
from workmind.modules.governance.models import AuditEvent
from workmind.modules.knowledge.models import Collection
from workmind.modules.knowledge.storage import LocalObjectStorage, object_key
from workmind.modules.meetings.models import Meeting
from workmind.modules.work.models import Notification

pytestmark = pytest.mark.skipif(not VALKEY_URL, reason="set TEST_VALKEY_URL to run")

EXPIRED = timedelta(days=45)  # older than a 30-day policy
KEPT = timedelta(days=29)  # younger: must survive
ROUTES = (
    ("GET", "/api/v1/admin/retention", None),
    ("PUT", "/api/v1/admin/retention", {"conversations_days": 30}),
    ("POST", "/api/v1/admin/retention/preview", None),
    ("POST", "/api/v1/admin/retention/run", {"confirm": "delete"}),
)


def _settings(database: Database, storage: Path) -> Settings:
    return Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        app_env="test",
        database_url=database.app_url,
        valkey_url=VALKEY_URL or "",
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
    assert {r.feature for r in registry.routers} >= {"retention_policies"}
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


# --- Without a license -------------------------------------------------------------------------


async def test_without_a_license_every_route_answers_402(community: TestClient, acme: Acme) -> None:
    for method, path, body in ROUTES:
        signed_out = community.request(method, path, json=body)
        response = community.request(method, path, json=body, headers=acme.as_("erin", write=True))
        assert signed_out.status_code == 401, path
        assert response.status_code == 402, (method, path)
        assert response.json()["code"] == "enterprise_feature"
        assert "'retention_policies'" in response.json()["detail"]


# --- With a license ----------------------------------------------------------------------------


@dataclass
class Data:
    """Per tenant: one expired and one kept item of each kind, plus things that never expire."""

    tenant_id: uuid.UUID
    expired_files: list[Path]
    kept_files: list[Path]
    expired_meeting: uuid.UUID


async def _populate(sessions: SessionFactory, acme: Acme, root: Path) -> Data:
    now = datetime.now(UTC)
    storage = LocalObjectStorage(root)
    user = acme.users["bob"]

    async def store(data: bytes) -> str:
        key = object_key(acme.tenant_id, hashlib.sha256(data).hexdigest())
        await storage.put(key, data)
        return key

    old_audio = await store(f"old recording {acme.tenant_id}".encode())
    new_audio = await store(f"new recording {acme.tenant_id}".encode())
    shared_audio = await store(f"shared recording {acme.tenant_id}".encode())
    transcript = await store(f"WEBVTT old transcript {acme.tenant_id}".encode())
    async with tenant_transaction(sessions, acme.tenant_id) as session:
        collection = (
            await session.scalars(select(Collection.id).where(Collection.key == "engineering-docs"))
        ).one()
        for age in (EXPIRED, KEPT):
            conversation = Conversation(
                tenant_id=acme.tenant_id, user_id=user, title="Q", last_message_at=now - age
            )
            session.add(conversation)
            await session.flush()
            session.add(
                Message(
                    tenant_id=acme.tenant_id,
                    conversation_id=conversation.id,
                    role="user",
                    content="hello",
                )
            )
            session.add(
                AnalyticsRun(
                    tenant_id=acme.tenant_id,
                    user_id=user,
                    question="Revenue?",
                    created_at=now - age,
                )
            )
            session.add(
                Notification(
                    tenant_id=acme.tenant_id,
                    user_id=user,
                    kind="task.assigned",
                    title="Read",
                    read_at=now - age,
                    created_at=now - age - timedelta(days=1),
                )
            )
        # Unread, however old: never deleted.
        session.add(
            Notification(
                tenant_id=acme.tenant_id,
                user_id=user,
                kind="task.assigned",
                title="Unread",
                created_at=now - timedelta(days=400),
            )
        )

        def meeting(filename: str, key: str, age: timedelta) -> Meeting:
            return Meeting(
                tenant_id=acme.tenant_id,
                title=filename,
                collection_id=collection,
                owner_id=user,
                filename=filename,
                media_key=key,
                status="ready",
                summary="We agreed on the plan.",
                segments=[{"id": "T1", "start": 0, "end": 1, "speaker": "A", "text": "Hi"}],
                created_at=now - age,
            )

        expired = meeting("standup-old.mp3", old_audio, EXPIRED)
        session.add_all(
            [
                expired,
                meeting("standup-new.mp3", new_audio, KEPT),
                # Same bytes as a kept recording: the file stays for the kept one.
                meeting("copy-old.m4a", shared_audio, EXPIRED),
                meeting("copy-new.m4a", shared_audio, KEPT),
                # A transcript upload is the transcript itself: kept, however old.
                meeting("notes-old.vtt", transcript, EXPIRED),
            ]
        )
        await session.flush()
        expired_id = expired.id
    return Data(
        tenant_id=acme.tenant_id,
        expired_files=[root / old_audio],
        kept_files=[root / new_audio, root / shared_audio, root / transcript],
        expired_meeting=expired_id,
    )


async def _counts(sessions: SessionFactory, tenant_id: uuid.UUID) -> dict[str, int]:
    async with tenant_transaction(sessions, tenant_id) as session:

        async def count(model: type[object], *where: object) -> int:
            query = select(func.count()).select_from(model).where(*where)  # type: ignore[arg-type]
            return int(await session.scalar(query) or 0)

        return {
            "conversations": await count(Conversation),
            "messages": await count(Message),
            "analytics_runs": await count(AnalyticsRun),
            "recordings": await count(Meeting, Meeting.media_deleted_at.is_(None)),
            "meetings": await count(Meeting),
            "notifications": await count(Notification),
            "audit_events": await count(AuditEvent),
        }


async def test_admins_set_a_policy_of_at_least_30_days(licensed: TestClient, acme: Acme) -> None:
    empty = licensed.get("/api/v1/admin/retention", headers=acme.as_("erin"))
    member = licensed.get("/api/v1/admin/retention", headers=acme.as_("bob"))
    too_short = licensed.put(
        "/api/v1/admin/retention",
        json={"conversations_days": 29},
        headers=acme.as_("erin", write=True),
    )
    saved = licensed.put(
        "/api/v1/admin/retention",
        json={"conversations_days": 365, "read_notifications_days": 30},
        headers=acme.as_("erin", write=True),
    )
    no_confirmation = licensed.post(
        "/api/v1/admin/retention/run", json={}, headers=acme.as_("erin", write=True)
    )

    assert empty.status_code == 200, empty.text
    assert empty.json()["conversations_days"] is None
    assert empty.json()["minimum_days"] == 30
    assert member.status_code == 403
    assert too_short.status_code == 422
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert (body["conversations_days"], body["read_notifications_days"]) == (365, 30)
    assert body["analytics_runs_days"] is None
    assert body["updated_by"]["display_name"] == "Erin Takahashi"
    assert no_confirmation.status_code == 422


async def test_preview_is_exact_and_a_run_deletes_only_this_tenants_expired_data(
    licensed: TestClient,
    acme: Acme,
    owner_sessions: SessionFactory,
    app_sessions: SessionFactory,
    tmp_path: Path,
) -> None:
    other_acme = await seed_acme(owner_sessions)
    ours = await _populate(app_sessions, acme, tmp_path)
    theirs = await _populate(app_sessions, other_acme, tmp_path)
    policy = {f"{kind}_days": 30 for kind in ("conversations", "analytics_runs")} | {
        "meeting_recordings_days": 30,
        "read_notifications_days": 30,
    }
    for tenant in (acme, other_acme):  # the other tenant has the same policy: only ours runs
        saved = licensed.put(
            "/api/v1/admin/retention", json=policy, headers=tenant.as_("erin", write=True)
        )
        assert saved.status_code == 200, saved.text
    before = await _counts(app_sessions, acme.tenant_id)
    before_theirs = await _counts(app_sessions, other_acme.tenant_id)

    preview = licensed.post("/api/v1/admin/retention/preview", headers=acme.as_("erin", write=True))
    run = licensed.post(
        "/api/v1/admin/retention/run",
        json={"confirm": "delete"},
        headers=acme.as_("erin", write=True),
    )
    after = await _counts(app_sessions, acme.tenant_id)
    after_theirs = await _counts(app_sessions, other_acme.tenant_id)

    assert preview.status_code == 200, preview.text
    expected = {
        "conversations": 1,
        "analytics_runs": 1,
        "meeting_recordings": 2,
        "read_notifications": 1,
    }
    assert preview.json()["would_delete"] == expected
    assert run.status_code == 200, run.text
    assert run.json()["deleted"] == expected  # exactly what the preview said
    assert run.json()["files_deleted"] == 1  # the shared recording's file is still used
    assert after == {
        **before,
        "conversations": before["conversations"] - 1,
        "messages": before["messages"] - 1,
        "analytics_runs": before["analytics_runs"] - 1,
        "recordings": before["recordings"] - 2,
        "notifications": before["notifications"] - 1,
        "audit_events": before["audit_events"] + 1,  # the run itself; nothing is ever deleted
    }
    assert after_theirs == before_theirs
    assert all(not f.exists() for f in ours.expired_files)
    assert all(f.exists() for f in ours.kept_files)
    assert all(f.exists() for f in theirs.expired_files + theirs.kept_files)

    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        meeting = await session.get(Meeting, ours.expired_meeting)
        runs = (
            await session.scalars(
                select(AuditEvent).where(AuditEvent.action == "admin.retention.run")
            )
        ).all()
    assert meeting is not None
    assert meeting.media_deleted_at is not None
    assert (meeting.summary, len(meeting.segments)) == ("We agreed on the plan.", 1)  # kept
    assert [(e.actor_id, e.details["trigger"], e.details["deleted"]) for e in runs] == [
        (acme.users["erin"], "manual", expected)
    ]
    shown = licensed.get(f"/api/v1/meetings/{ours.expired_meeting}", headers=acme.as_("bob"))
    media = licensed.get(f"/api/v1/meetings/{ours.expired_meeting}/media", headers=acme.as_("bob"))
    assert shown.json()["has_media"] is False
    assert shown.json()["media_deleted_at"] is not None
    assert media.status_code == 410

    again = licensed.post("/api/v1/admin/retention/preview", headers=acme.as_("erin", write=True))
    assert again.json()["would_delete"] == dict.fromkeys(expected, 0)


async def test_the_daily_task_applies_every_tenants_policy(
    licensed: TestClient, acme: Acme, app_sessions: SessionFactory, tmp_path: Path
) -> None:
    from workmind_ee import retention  # the licensed fixture skipped without the package

    await _populate(app_sessions, acme, tmp_path)
    licensed.put(
        "/api/v1/admin/retention",
        json={"conversations_days": 30},
        headers=acme.as_("erin", write=True),
    )
    storage = LocalObjectStorage(tmp_path)
    scheduled, had_policy = await retention.apply_policy(
        app_sessions, storage, acme.tenant_id, actor=retention.Actor.system(), trigger="schedule"
    )
    nothing, no_policy = await retention.apply_policy(
        app_sessions, storage, uuid.uuid4(), actor=retention.Actor.system(), trigger="schedule"
    )
    policy = licensed.get("/api/v1/admin/retention", headers=acme.as_("erin")).json()

    assert had_policy
    assert scheduled.deleted.conversations == 1
    assert scheduled.deleted.analytics_runs == 0  # no period set: kept forever
    assert policy["last_run"]["trigger"] == "schedule"
    assert policy["last_run_at"] is not None
    assert not no_policy
    assert nothing.deleted.conversations == 0
