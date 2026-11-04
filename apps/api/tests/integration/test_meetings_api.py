"""Meetings over real HTTP: a transcript becomes notes, tasks and a searchable document, with the
meeting's collection deciding who sees and who changes it.

Needs TEST_QDRANT_URL, the database and Valkey. The model is scripted (FakeLLM); ingestion runs
inline with hash embeddings, like the Knowledge Hub tests.
"""

import asyncio
import os
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.fakes import FakeLLM
from tests.integration.conftest import VALKEY_URL, Acme, Database
from workmind.core.config import Settings
from workmind.main import create_app
from workmind.modules.meetings.extract import ActionItem, Decision, Extraction
from workmind.modules.retrieval.qdrant import QdrantClient
from workmind.providers.transcription import DisabledTranscriber

QDRANT_URL = os.environ.get("TEST_QDRANT_URL")
pytestmark = pytest.mark.skipif(
    not (QDRANT_URL and VALKEY_URL), reason="set TEST_QDRANT_URL and TEST_VALKEY_URL"
)
VTT = b"""WEBVTT

00:00:01.000 --> 00:00:05.000
<v Olivia Grant>We agreed: the Rotterdam safety drill moves to Tuesday mornings.

00:00:06.000 --> 00:00:10.000
<v Wei Zhang>I'll add battery-temperature alerts to the dashboard by Friday next week.

00:05:30.000 --> 00:05:34.000
<v Olivia Grant>And I'll update the SOP by Thursday.
"""
OUTCOME = Extraction(
    summary="The drill moves to Tuesday; Wei adds battery alerts.",
    decisions=[Decision(text="Move the Rotterdam safety drill to Tuesday mornings", segment="T1")],
    action_items=[
        ActionItem(
            text="Add battery-temperature alerts to the FleetOS dashboard",
            owner="Wei Zhang",
            due="2026-11-13",
            segment="T2",
        ),
        ActionItem(text="Update the operations SOP", owner="Olivia", due="", segment="T3"),
    ],
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


def _model(api: TestClient, *replies: Extraction) -> FakeLLM:
    llm = FakeLLM(structured=list(replies))
    app: Any = api.app
    app.state.resources.llm = llm
    return llm


def _upload(
    api: TestClient, acme: Acme, who: str, collection: str, filename: str, data: bytes = VTT
) -> Any:  # a Starlette TestClient response (httpx2 when installed)
    return api.post(
        "/api/v1/meetings",
        params={"collection": collection, "filename": filename, "title": "Weekly ops sync"},
        content=data,
        headers={**acme.as_(who, write=True), "Content-Type": "application/octet-stream"},
    )


def _wait(api: TestClient, acme: Acme, who: str, meeting_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        body = api.get(f"/api/v1/meetings/{meeting_id}", headers=acme.as_(who)).json()
        if body["status"] in ("ready", "failed"):
            return body
        time.sleep(0.2)
    raise AssertionError("meeting processing did not finish")


async def test_a_transcript_becomes_notes_tasks_and_commitments(
    api: TestClient, acme: Acme
) -> None:
    llm = _model(api, OUTCOME)

    uploaded = _upload(api, acme, "olivia.grant", "operations-sops", "ops-sync.vtt")
    assert uploaded.status_code == 202, uploaded.text
    meeting = _wait(api, acme, "olivia.grant", uploaded.json()["id"])

    assert meeting["status"] == "ready", meeting["error"]
    assert [s["speaker"] for s in meeting["segments"]] == [
        "Olivia Grant",
        "Wei Zhang",
        "Olivia Grant",
    ]
    assert meeting["speakers"]["Wei Zhang"]["user_id"] == str(acme.users["wei.zhang"])
    assert meeting["decisions"][0]["start"] == 1.0
    wei_item, olivia_item = meeting["action_items"]
    assert (wei_item["owner_user_id"], wei_item["due"], wei_item["start"]) == (
        str(acme.users["wei.zhang"]),
        "2026-11-13",
        6.0,
    )
    assert olivia_item["owner_user_id"] == str(acme.users["olivia.grant"])
    assert meeting["can_edit"] is True
    assert [role for role, _ in llm.requests] == ["generator"]

    # The transcript is a document of the meeting's collection, linked back to the meeting.
    document = api.get(f"/api/v1/documents/{meeting['document_id']}", headers=acme.as_("bob"))
    assert document.status_code == 200
    assert document.json()["title"].startswith("Meeting: Weekly ops sync")
    notifications = api.get("/api/v1/notifications", headers=acme.as_("olivia.grant")).json()
    assert any(n["kind"] == "meeting.ready" for n in notifications)

    # Viewers of the collection read it; they do not change it. Outsiders do not see it at all.
    as_bob = api.get(f"/api/v1/meetings/{meeting['id']}", headers=acme.as_("bob")).json()
    assert as_bob["can_edit"] is False
    assert (
        api.post(
            f"/api/v1/meetings/{meeting['id']}/actions/0/task", headers=acme.as_("bob", write=True)
        ).status_code
        == 403
    )
    assert (
        api.get(f"/api/v1/meetings/{meeting['id']}", headers=acme.as_("mallory")).status_code == 404
    )
    assert meeting["id"] not in {
        m["id"] for m in api.get("/api/v1/meetings", headers=acme.as_("mallory")).json()
    }

    # An action item becomes Wei's task, once, and shows up in his commitments.
    created = api.post(
        f"/api/v1/meetings/{meeting['id']}/actions/0/task",
        headers=acme.as_("olivia.grant", write=True),
    )
    assert created.status_code == 201, created.text
    again = api.post(
        f"/api/v1/meetings/{meeting['id']}/actions/0/task",
        headers=acme.as_("olivia.grant", write=True),
    )
    assert again.status_code == 409
    commitments = api.get("/api/v1/me/commitments", headers=acme.as_("wei.zhang")).json()
    mine = [c for c in commitments if c["meeting_id"] == meeting["id"]]
    assert [(c["text"], c["due"], c["task_status"]) for c in mine] == [
        ("Add battery-temperature alerts to the FleetOS dashboard", "2026-11-13", "open")
    ]
    wei_notes = api.get("/api/v1/notifications", headers=acme.as_("wei.zhang")).json()
    assert any(n["kind"] == "task.assigned" for n in wei_notes)


async def test_speaker_mapping_relinks_owners(api: TestClient, acme: Acme) -> None:
    unnamed = b"WEBVTT\n\n00:00:01.000 --> 00:00:04.000\nSPEAKER_1: I'll send the quote today.\n"
    _model(
        api,
        Extraction(
            summary="",
            decisions=[],
            action_items=[
                ActionItem(text="Send the quote", owner="SPEAKER_1", due="", segment="T1")
            ],
        ),
    )
    meeting = _wait(
        api,
        acme,
        "olivia.grant",
        _upload(api, acme, "olivia.grant", "operations-sops", "call.vtt", unnamed).json()["id"],
    )
    assert meeting["action_items"][0]["owner_user_id"] is None

    mapped = api.put(
        f"/api/v1/meetings/{meeting['id']}/speakers",
        json={"speakers": {"SPEAKER_1": str(acme.users["wei.zhang"])}},
        headers=acme.as_("olivia.grant", write=True),
    )

    assert mapped.status_code == 200, mapped.text
    assert mapped.json()["speakers"]["SPEAKER_1"]["name"] == "Wei Zhang"
    assert mapped.json()["action_items"][0]["owner_user_id"] == str(acme.users["wei.zhang"])
    fixed = api.patch(
        f"/api/v1/meetings/{meeting['id']}/actions/0",
        json={"owner_user_id": str(acme.users["olivia.grant"]), "due": "2026-11-06"},
        headers=acme.as_("olivia.grant", write=True),
    ).json()["action_items"][0]
    assert (fixed["owner"], fixed["owner_user_id"], fixed["due"], fixed["text"]) == (
        "Olivia Grant",
        str(acme.users["olivia.grant"]),
        "2026-11-06",
        "Send the quote",  # not sent: unchanged
    )
    assert (
        api.patch(
            f"/api/v1/meetings/{meeting['id']}/actions/0",
            json={"text": "Send the quote"},
            headers=acme.as_("bob", write=True),
        ).status_code
        == 403
    )
    assert (
        api.put(
            f"/api/v1/meetings/{meeting['id']}/speakers",
            json={"speakers": {"SPEAKER_9": None}},
            headers=acme.as_("olivia.grant", write=True),
        ).status_code
        == 422
    )


async def test_restricted_meetings_and_rejected_uploads(api: TestClient, acme: Acme) -> None:
    _model(api, Extraction(summary="Strategy.", decisions=[], action_items=[]))

    assert _upload(api, acme, "bob", "operations-sops", "sync.vtt").status_code == 403  # a viewer
    assert _upload(api, acme, "olivia.grant", "operations-sops", "sync.exe").status_code == 415
    assert _upload(api, acme, "olivia.grant", "operations-sops", "sync.vtt", b"").status_code == 422
    assert _upload(api, acme, "olivia.grant", "no-such-collection", "sync.vtt").status_code == 404

    secret = _upload(api, acme, "olivia.grant", "leadership-strategy", "board-prep.vtt").json()
    assert _wait(api, acme, "olivia.grant", secret["id"])["status"] == "ready"
    assert api.get(f"/api/v1/meetings/{secret['id']}", headers=acme.as_("bob")).status_code == 404
    assert secret["id"] not in {
        m["id"] for m in api.get("/api/v1/meetings", headers=acme.as_("bob")).json()
    }


async def test_a_recording_that_cannot_be_transcribed_fails_visibly(
    api: TestClient, acme: Acme
) -> None:
    app: Any = api.app
    app.state.resources.transcriber = DisabledTranscriber()
    llm = _model(api)

    uploaded = _upload(api, acme, "olivia.grant", "operations-sops", "standup.m4a", b"\x00audio")
    meeting = _wait(api, acme, "olivia.grant", uploaded.json()["id"])
    media = api.get(f"/api/v1/meetings/{meeting['id']}/media", headers=acme.as_("olivia.grant"))

    assert meeting["status"] == "failed"
    assert "transcription is off" in (meeting["error"] or "")
    assert llm.requests == []  # nothing to extract from
    assert (media.status_code, media.headers["content-type"], media.content) == (
        200,
        "audio/mp4",
        b"\x00audio",
    )
