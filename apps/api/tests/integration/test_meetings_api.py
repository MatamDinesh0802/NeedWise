"""Meetings over real HTTP: a transcript becomes notes, tasks and a searchable document, with the
meeting's collection deciding who sees and who changes it.

Needs TEST_QDRANT_URL, the database and Valkey. The model is scripted (FakeLLM); ingestion runs
inline with hash embeddings, like the Knowledge Hub tests.
"""

import asyncio
import os
import time
import uuid
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from needwise.core.config import Settings
from needwise.core.tenancy import tenant_transaction
from needwise.main import create_app
from needwise.modules.governance.models import AuditEvent
from needwise.modules.meetings.extract import ActionItem, Decision, Extraction
from needwise.modules.meetings.topics import Outline, TopicStart
from needwise.modules.retrieval.qdrant import QdrantClient
from needwise.providers.diarization import DisabledDiarizer, Turn
from needwise.providers.transcription import DisabledTranscriber, Transcript
from needwise.providers.transcription import Segment as TranscriptSegment
from tests.fakes import FakeLLM
from tests.integration.conftest import VALKEY_URL, Acme, Database, SessionFactory

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


def _model(api: TestClient, *replies: Extraction | Outline) -> FakeLLM:
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
    # Extraction, then the topic outline (both the extraction's role). The outline was not
    # scripted, so it came from the transcript's words, and the meeting says so.
    assert [role for role, _ in llm.requests] == ["generator", "generator"]
    assert [t["source"] for t in meeting["topics"]] == ["words"]
    assert meeting["topics"][0]["start"] == 1.0
    assert meeting["timings"]["topics_fallback"].startswith("the model's outline failed")

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
    task_id = created.json()["action_items"][0]["task_id"]
    task = api.get(f"/api/v1/tasks/{task_id}", headers=acme.as_("wei.zhang"))
    assert (task.status_code, task.json()["assignee_id"]) == (200, str(acme.users["wei.zhang"]))
    assert api.get(f"/api/v1/tasks/{task_id}", headers=acme.as_("bob")).status_code == 404
    wei_notes = api.get("/api/v1/notifications", headers=acme.as_("wei.zhang")).json()
    assert any(n["kind"] == "task.assigned" for n in wei_notes)

    # Wei hands it over to Olivia, who is told; a colleague who doesn't exist is refused.
    url, as_wei = f"/api/v1/tasks/{task_id}", acme.as_("wei.zhang", write=True)
    nobody = api.patch(url, json={"assignee_id": str(uuid.uuid4())}, headers=as_wei)
    handed = api.patch(url, json={"assignee_id": str(acme.users["olivia.grant"])}, headers=as_wei)
    assert nobody.status_code == 422
    assert (handed.status_code, handed.json()["assignee_id"]) == (
        200,
        str(acme.users["olivia.grant"]),
    )
    olivia_notes = api.get("/api/v1/notifications", headers=acme.as_("olivia.grant")).json()
    assert any(
        n["kind"] == "task.assigned" and n["link"] == f"/tasks/{task_id}" for n in olivia_notes
    )


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
    item = mapped.json()["action_items"][0]
    # The item names its new owner, not the label it was extracted with.
    assert (item["owner"], item["owner_user_id"]) == ("Wei Zhang", str(acme.users["wei.zhang"]))
    remapped = api.put(
        f"/api/v1/meetings/{meeting['id']}/speakers",
        json={"speakers": {"SPEAKER_1": str(acme.users["noah.fischer"])}},
        headers=acme.as_("olivia.grant", write=True),
    ).json()["action_items"][0]
    assert (remapped["owner"], remapped["owner_user_id"]) == (
        "Noah Fischer",
        str(acme.users["noah.fischer"]),
    )
    unmapped = api.put(
        f"/api/v1/meetings/{meeting['id']}/speakers",
        json={"speakers": {"SPEAKER_1": None}},
        headers=acme.as_("olivia.grant", write=True),
    ).json()["action_items"][0]
    assert (unmapped["owner"], unmapped["owner_user_id"]) == ("SPEAKER_1", None)
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
    # Players seek with Range requests; without a 206 browsers can't jump to a timestamp.
    path, me = f"/api/v1/meetings/{meeting['id']}/media", acme.as_("olivia.grant")
    part = api.get(path, headers={**me, "Range": "bytes=1-3"})
    past = api.get(path, headers={**me, "Range": "bytes=50-"})
    assert media.headers["accept-ranges"] == "bytes"
    assert (part.status_code, part.headers["content-range"], part.content) == (
        206,
        "bytes 1-3/6",
        b"aud",
    )
    assert (past.status_code, past.headers["content-range"]) == (416, "bytes */6")


async def test_members_look_up_colleagues_and_guests_cannot(api: TestClient, acme: Acme) -> None:
    found = api.get("/api/v1/people", params={"q": "zhang"}, headers=acme.as_("olivia.grant"))

    assert found.status_code == 200
    assert [(p["display_name"], p["departments"]) for p in found.json()] == [
        ("Wei Zhang", ["Engineering"])
    ]
    everyone = api.get("/api/v1/people", headers=acme.as_("bob")).json()
    assert len(everyone) == len(acme.users)
    assert api.get("/api/v1/people", headers=acme.as_("mallory")).status_code == 403


DIARIZED_VTT = b"""WEBVTT

00:00:01.000 --> 00:00:05.000
SPEAKER_1: Can you send Bluefin the quote for the spare packs?

00:00:06.000 --> 00:00:10.000
SPEAKER_2: Yes, I'll send the quote by Friday.
"""
QUOTE = Extraction(
    summary="SPEAKER_1 asked SPEAKER_2 for the Bluefin quote.",
    decisions=[Decision(text="SPEAKER_2 owns the Bluefin quote", segment="T2")],
    action_items=[
        ActionItem(text="SPEAKER_2 sends the quote", owner="SPEAKER_2", due="", segment="T2")
    ],
)
QUOTE_TOPICS = Outline(
    topics=[TopicStart(title="The quote SPEAKER_2 owes Bluefin", first_segment="T1")]
)


def _speakers(api: TestClient, acme: Acme, who: str, meeting_id: str, mapping: Any) -> Any:
    return api.put(
        f"/api/v1/meetings/{meeting_id}/speakers",
        json={"speakers": mapping},
        headers=acme.as_(who, write=True),
    )


def _chunk_text(api: TestClient, acme: Acme, document_id: str, expect: str) -> str:
    """The indexed transcript's chunks, once they contain `expect` (indexing runs inline)."""
    deadline, text = time.monotonic() + 30, ""
    while time.monotonic() < deadline:
        chunks = api.get(
            f"/api/v1/documents/{document_id}/chunks", headers=acme.as_("olivia.grant")
        )
        text = " ".join(c["text"] for c in chunks.json()["items"])
        if expect in text:
            break
        time.sleep(0.2)
    return text


async def test_naming_speakers_relabels_the_transcript_the_notes_and_search(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    _model(api, QUOTE, QUOTE_TOPICS)
    uploaded = _upload(api, acme, "olivia.grant", "leadership-strategy", "quote.vtt", DIARIZED_VTT)
    meeting = _wait(api, acme, "olivia.grant", uploaded.json()["id"])
    meeting_id, hannah = meeting["id"], str(acme.users["hannah.schmidt"])

    assert meeting["status"] == "ready", meeting["error"]
    assert meeting["topics"] == [
        {
            "title": "The quote SPEAKER_2 owes Bluefin",
            "segment": "T1",
            "start": 1.0,
            "end": 10.0,
            "source": "model",
        }
    ]
    assert meeting["can_map_speakers"] is True  # the uploader
    # Hannah can edit the collection, but nobody said she was there: she may not name voices.
    as_hannah = api.get(f"/api/v1/meetings/{meeting_id}", headers=acme.as_("hannah.schmidt"))
    assert (as_hannah.json()["can_edit"], as_hannah.json()["can_map_speakers"]) == (True, False)
    refused = _speakers(api, acme, "hannah.schmidt", meeting_id, {"SPEAKER_1": hannah})
    assert refused.status_code == 403

    named = _speakers(
        api,
        acme,
        "olivia.grant",
        meeting_id,
        {"SPEAKER_1": hannah, "SPEAKER_2": {"name": "  Jonas   Brandt "}},
    )

    assert named.status_code == 200, named.text
    body = named.json()
    assert body["speakers"] == {
        "SPEAKER_1": {"name": "Hannah Schmidt", "user_id": hannah},
        "SPEAKER_2": {"name": "Jonas Brandt", "user_id": None, "guest": True},
    }
    # What the model wrote with the labels now reads with the names.
    assert body["summary"] == "Hannah Schmidt asked Jonas Brandt for the Bluefin quote."
    assert body["decisions"][0]["text"] == "Jonas Brandt owns the Bluefin quote"
    assert body["topics"][0]["title"] == "The quote Jonas Brandt owes Bluefin"
    item = body["action_items"][0]
    assert (item["text"], item["owner"], item["owner_user_id"]) == (
        "Jonas Brandt sends the quote",
        "Jonas Brandt",
        None,
    )
    assert [s["speaker"] for s in body["segments"]] == ["SPEAKER_1", "SPEAKER_2"]  # labels kept

    # The indexed transcript is a new version with the names, and search reads them.
    document_id = body["document_id"]
    document = api.get(f"/api/v1/documents/{document_id}", headers=acme.as_("olivia.grant"))
    assert len(document.json()["versions"]) == 2
    text = _chunk_text(api, acme, document_id, "Hannah Schmidt:")
    assert "Hannah Schmidt: Can you send Bluefin" in text
    assert "Jonas Brandt: Yes, I'll send the quote" in text
    assert "SPEAKER_1" not in text

    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        (event,) = (
            await session.scalars(
                select(AuditEvent).where(
                    AuditEvent.action == "meeting.speakers_mapped",
                    AuditEvent.resource_id == meeting_id,
                )
            )
        ).all()
    assert event.actor_id == acme.users["olivia.grant"]
    assert event.details == {
        "speakers": {"SPEAKER_1": hannah, "SPEAKER_2": "guest"},  # never the guest's name
        "action_items_relinked": 1,
        "transcript_version": 2,
    }

    # Now Hannah is an attendee who can edit: she may name the others.
    as_hannah = api.get(f"/api/v1/meetings/{meeting_id}", headers=acme.as_("hannah.schmidt"))
    assert as_hannah.json()["can_map_speakers"] is True
    marcus = str(acme.users["marcus.bell"])
    renamed = _speakers(api, acme, "hannah.schmidt", meeting_id, {"SPEAKER_2": marcus}).json()
    assert (renamed["action_items"][0]["owner"], renamed["action_items"][0]["owner_user_id"]) == (
        "Marcus Bell",
        marcus,
    )
    unnamed = _speakers(api, acme, "hannah.schmidt", meeting_id, {"SPEAKER_2": None}).json()
    assert unnamed["speakers"]["SPEAKER_2"] == {"name": "SPEAKER_2", "user_id": None}
    assert unnamed["summary"] == "Hannah Schmidt asked SPEAKER_2 for the Bluefin quote."
    assert (unnamed["action_items"][0]["owner"], unnamed["action_items"][0]["owner_user_id"]) == (
        "SPEAKER_2",
        None,
    )
    # Nothing to change: no new transcript version.
    assert (
        _speakers(api, acme, "hannah.schmidt", meeting_id, {"SPEAKER_2": None}).status_code == 200
    )
    document = api.get(f"/api/v1/documents/{document_id}", headers=acme.as_("olivia.grant"))
    assert len(document.json()["versions"]) == 4


async def test_admins_who_see_a_meeting_may_name_its_speakers_viewers_may_not(
    api: TestClient, acme: Acme
) -> None:
    _model(api, QUOTE)
    uploaded = _upload(api, acme, "olivia.grant", "operations-sops", "call.vtt", DIARIZED_VTT)
    meeting_id = _wait(api, acme, "olivia.grant", uploaded.json()["id"])["id"]
    wei = str(acme.users["wei.zhang"])

    as_erin = api.get(f"/api/v1/meetings/{meeting_id}", headers=acme.as_("erin")).json()
    as_bob = api.get(f"/api/v1/meetings/{meeting_id}", headers=acme.as_("bob")).json()
    assert (as_erin["can_edit"], as_erin["can_map_speakers"]) == (False, True)  # an admin
    assert (as_bob["can_edit"], as_bob["can_map_speakers"]) == (False, False)  # a viewer
    assert _speakers(api, acme, "bob", meeting_id, {"SPEAKER_1": wei}).status_code == 403
    assert _speakers(api, acme, "mallory", meeting_id, {"SPEAKER_1": wei}).status_code == 404
    blank = _speakers(api, acme, "erin", meeting_id, {"SPEAKER_1": {"name": "   "}})
    stranger = _speakers(api, acme, "erin", meeting_id, {"SPEAKER_1": str(uuid.uuid4())})
    assert (blank.status_code, blank.json()["code"]) == (422, "empty_name")
    assert (stranger.status_code, stranger.json()["code"]) == (422, "unknown_user")

    named = _speakers(api, acme, "erin", meeting_id, {"SPEAKER_2": wei})

    assert named.status_code == 200, named.text
    item = named.json()["action_items"][0]
    assert (item["owner"], item["owner_user_id"]) == ("Wei Zhang", wei)


class _Transcriber:
    """Speech to text without a model: two people, no speaker labels (as Whisper gives them)."""

    model_id = "scripted"

    async def transcribe(
        self, data: bytes, filename: str, *, vocabulary: Sequence[str] = ()
    ) -> Transcript:
        return Transcript(
            [
                TranscriptSegment(0.5, 4.0, "Can you send Bluefin the quote?"),
                TranscriptSegment(4.6, 8.0, "Yes, by Friday."),
                TranscriptSegment(8.4, 10.0, "Thanks."),
            ],
            "en",
            10.0,
        )


class _Diarizer:
    model_id = "scripted-diarizer"

    async def diarize(self, data: bytes, filename: str) -> list[Turn]:
        return [Turn(0.3, 4.2, 7), Turn(4.5, 8.1, 2), Turn(8.3, 10.0, 7)]


async def test_a_recording_gets_speaker_labels_from_diarization(
    api: TestClient, acme: Acme
) -> None:
    app: Any = api.app
    app.state.resources.transcriber = _Transcriber()
    app.state.resources.diarizer = _Diarizer()
    _model(api, Extraction(summary="A quote.", decisions=[], action_items=[]))

    uploaded = _upload(api, acme, "olivia.grant", "operations-sops", "call.m4a", b"\x00audio")
    meeting = _wait(api, acme, "olivia.grant", uploaded.json()["id"])

    assert meeting["status"] == "ready", meeting["error"]
    assert [s["speaker"] for s in meeting["segments"]] == ["SPEAKER_1", "SPEAKER_2", "SPEAKER_1"]
    assert meeting["speakers"] == {
        "SPEAKER_1": {"name": "SPEAKER_1", "user_id": None},
        "SPEAKER_2": {"name": "SPEAKER_2", "user_id": None},
    }
    assert (meeting["timings"]["diarizer"], meeting["timings"]["speakers_found"]) == (
        "scripted-diarizer",
        2,
    )

    # Diarization off: the recording is still transcribed, unlabelled, and says why.
    app.state.resources.diarizer = DisabledDiarizer()
    _model(api, Extraction(summary="A quote.", decisions=[], action_items=[]))
    plain = _upload(api, acme, "olivia.grant", "operations-sops", "call-2.m4a", b"\x01audio")
    plain_meeting = _wait(api, acme, "olivia.grant", plain.json()["id"])
    assert plain_meeting["status"] == "ready", plain_meeting["error"]
    assert {s["speaker"] for s in plain_meeting["segments"]} == {None}
    assert plain_meeting["speakers"] == {}
    assert "DIARIZATION_PROVIDER=none" in plain_meeting["timings"]["diarization_skipped"]
