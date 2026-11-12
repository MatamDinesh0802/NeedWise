"""Deleting a meeting over real HTTP: the meeting, its file and its indexed transcript go (search
included); tasks made from it stay with their assignee without the link; only the uploader and
the collection's editors may do it; everything is audited.

Needs TEST_QDRANT_URL, the database and Valkey. The model is scripted (FakeLLM); ingestion runs
inline with hash embeddings, like the Knowledge Hub tests.
"""

import asyncio
import hashlib
import os
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete as sa_delete
from sqlalchemy import select, update

from tests.fakes import FakeLLM
from tests.integration.conftest import VALKEY_URL, Acme, Database, SessionFactory
from tests.integration.test_meetings_api import OUTCOME, VTT, _model, _upload, _wait
from workmind.core.config import Settings
from workmind.core.tenancy import tenant_transaction
from workmind.main import create_app
from workmind.modules.governance.models import AuditEvent
from workmind.modules.knowledge.models import Collection, Document
from workmind.modules.knowledge.storage import MemoryObjectStorage, object_key
from workmind.modules.meetings.models import Meeting
from workmind.modules.meetings.pipeline import MeetingDeps, process_meeting
from workmind.modules.retrieval.qdrant import QdrantClient, match
from workmind.providers.llm import LLMRequest, Role
from workmind.providers.transcription import DisabledTranscriber

QDRANT_URL = os.environ.get("TEST_QDRANT_URL")
pytestmark = pytest.mark.skipif(
    not (QDRANT_URL and VALKEY_URL), reason="set TEST_QDRANT_URL and TEST_VALKEY_URL"
)


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
        transcription_provider="none",
    )
    with TestClient(create_app(settings)) as client:
        yield client


@pytest.fixture
async def qdrant(collection_name: str) -> AsyncIterator[tuple[QdrantClient, str]]:
    async with httpx.AsyncClient() as http:
        yield QdrantClient(http, QDRANT_URL or ""), f"chunks_{collection_name}_64"


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


def ready_meeting(api: TestClient, acme: Acme, filename: str, data: bytes = VTT) -> dict[str, Any]:
    _model(api, OUTCOME)
    uploaded = _upload(api, acme, "olivia.grant", "operations-sops", filename, data)
    assert uploaded.status_code == 202, uploaded.text
    meeting = _wait(api, acme, "olivia.grant", uploaded.json()["id"])
    assert meeting["status"] == "ready", meeting["error"]
    # The notes are ready before the transcript is indexed (ingestion runs after).
    deadline = time.monotonic() + 20
    document = f"/api/v1/documents/{meeting['document_id']}"
    while api.get(document, headers=acme.as_("olivia.grant")).json()["status"] != "ready":
        assert time.monotonic() < deadline, "the transcript was not indexed"
        time.sleep(0.2)
    return meeting


def delete(api: TestClient, acme: Acme, who: str, meeting_id: str) -> int:
    return api.delete(
        f"/api/v1/meetings/{meeting_id}", headers=acme.as_(who, write=True)
    ).status_code


async def uploaded_by(sessions: SessionFactory, acme: Acme, meeting_id: str, who: str) -> None:
    """As if `who` had uploaded it (when they still were an editor of the collection)."""
    async with tenant_transaction(sessions, acme.tenant_id) as session:
        await session.execute(
            update(Meeting)
            .where(Meeting.id == uuid.UUID(meeting_id))
            .values(owner_id=acme.users[who])
        )


async def test_deleting_a_meeting_removes_it_its_file_and_its_transcript_but_keeps_tasks(
    api: TestClient,
    acme: Acme,
    app_sessions: SessionFactory,
    qdrant: tuple[QdrantClient, str],
    tmp_path: Path,
) -> None:
    client, points = qdrant
    meeting = ready_meeting(api, acme, "ops-sync-2026-11-02.vtt")
    meeting_id, document_id = meeting["id"], meeting["document_id"]
    task = api.post(
        f"/api/v1/meetings/{meeting_id}/actions/0/task",
        headers=acme.as_("olivia.grant", write=True),
    ).json()["action_items"][0]["task_id"]
    media = tmp_path / object_key(acme.tenant_id, hashlib.sha256(VTT).hexdigest())
    in_index = {"must": [match("document_id", document_id)]}
    assert media.exists()
    assert await client.count(points, in_index) > 0

    # Viewers can't; people outside the collection don't even see it.
    assert delete(api, acme, "bob", meeting_id) == 403
    assert delete(api, acme, "mallory", meeting_id) == 404

    assert delete(api, acme, "olivia.grant", meeting_id) == 204  # she uploaded it

    me = acme.as_("olivia.grant")
    assert api.get(f"/api/v1/meetings/{meeting_id}", headers=me).status_code == 404
    assert api.get(f"/api/v1/documents/{document_id}", headers=me).status_code == 404
    assert await client.count(points, in_index) == 0
    assert not media.exists()
    assert meeting_id not in {m["id"] for m in api.get("/api/v1/meetings", headers=me).json()}
    # Wei's task stays his; it names the meeting it came from, without a link to it.
    kept = api.get(f"/api/v1/tasks/{task}", headers=acme.as_("wei.zhang")).json()
    assert kept["assignee_id"] == str(acme.users["wei.zhang"])
    assert kept["origin"] == {
        "via": "meeting",
        "meeting_title": "Weekly ops sync",
        "meeting_deleted": True,
    }
    commitments = api.get("/api/v1/me/commitments", headers=acme.as_("wei.zhang")).json()
    assert all(c["meeting_id"] != meeting_id for c in commitments)
    notes = api.get("/api/v1/notifications", headers=me).json()
    assert not any(n["link"] == f"/meetings/{meeting_id}" for n in notes)
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        audit = {
            e.action: e
            for e in await session.scalars(
                select(AuditEvent).where(
                    AuditEvent.action.in_(("meeting.deleted", "knowledge.document.deleted"))
                )
            )
        }
    assert audit["meeting.deleted"].resource_id == meeting_id
    assert audit["meeting.deleted"].actor_id == acme.users["olivia.grant"]
    assert audit["meeting.deleted"].details["tasks_unlinked"] == 1
    assert audit["knowledge.document.deleted"].resource_id == document_id
    assert delete(api, acme, "olivia.grant", meeting_id) == 404  # already gone


async def test_the_uploader_and_the_editors_may_delete_and_a_shared_file_stays(
    api: TestClient, acme: Acme, app_sessions: SessionFactory, tmp_path: Path
) -> None:
    first = ready_meeting(api, acme, "standup-a.vtt")
    second = ready_meeting(api, acme, "standup-b.vtt")  # the same bytes: one stored file
    media = tmp_path / object_key(acme.tenant_id, hashlib.sha256(VTT).hexdigest())
    # Both uploaded by Ivan, an operations member who can only view the collection now.
    await uploaded_by(app_sessions, acme, first["id"], "ivan.petrov")
    await uploaded_by(app_sessions, acme, second["id"], "ivan.petrov")

    assert delete(api, acme, "ivan.petrov", first["id"]) == 204  # the uploader
    assert media.exists()  # the other meeting still uses the file
    assert delete(api, acme, "bob", second["id"]) == 403  # a viewer who didn't upload it
    assert delete(api, acme, "olivia.grant", second["id"]) == 204  # an editor
    assert not media.exists()


class NoDispatch:
    async def dispatch(
        self, tenant_id: uuid.UUID, document_id: uuid.UUID, version_id: uuid.UUID
    ) -> None:
        return None


async def test_a_meeting_deleted_while_it_is_processed_leaves_no_transcript_behind(
    app_sessions: SessionFactory, acme: Acme
) -> None:
    storage = MemoryObjectStorage()
    key = object_key(acme.tenant_id, hashlib.sha256(VTT).hexdigest())
    await storage.put(key, VTT)
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        collection = (
            await session.scalars(select(Collection).where(Collection.key == "operations-sops"))
        ).one()
        meeting = Meeting(
            tenant_id=acme.tenant_id,
            title="Deleted mid-way",
            collection_id=collection.id,
            owner_id=acme.users["olivia.grant"],
            filename="deleted-mid-way.vtt",
            media_key=key,
        )
        session.add(meeting)
        await session.flush()
        meeting_id = meeting.id

    class DeletesFirst(FakeLLM):
        """Someone deletes the meeting while the model writes its notes."""

        async def structured(self, role: Role, request: LLMRequest, schema: Any) -> Any:
            async with tenant_transaction(app_sessions, acme.tenant_id) as session:
                await session.execute(sa_delete(Meeting).where(Meeting.id == meeting_id))
            return await super().structured(role, request, schema)

    deps = MeetingDeps(
        app_sessions,
        storage,
        DeletesFirst(structured=[OUTCOME]),  # type: ignore[arg-type]
        DisabledTranscriber(),
        NoDispatch(),
    )
    await process_meeting(deps, acme.tenant_id, meeting_id)  # ends quietly

    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        transcripts = (
            await session.scalars(
                select(Document).where(Document.filename.like("meeting-deleted-mid-way-%"))
            )
        ).all()
    assert transcripts == []
    await process_meeting(deps, acme.tenant_id, meeting_id)  # already gone: nothing to do
