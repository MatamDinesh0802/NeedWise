"""Processing one meeting: transcribe (or read the transcript), and for a recording tell its
speakers apart -> extract -> outline the topics -> index the transcript as a document of the
meeting's collection, so it is searchable with that collection's access."""

import asyncio
import re
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from workmind.core.context import RequestContext
from workmind.core.tenancy import tenant_transaction
from workmind.modules.governance.audit import Actor, record_event
from workmind.modules.identity.models import Tenant, User
from workmind.modules.knowledge import service
from workmind.modules.knowledge.dispatch import Dispatcher
from workmind.modules.knowledge.models import Collection
from workmind.modules.knowledge.service import UploadResult
from workmind.modules.knowledge.storage import ObjectStorage, owned_key
from workmind.modules.meetings import topics, transcripts
from workmind.modules.meetings.extract import clock, extract, speaker_name
from workmind.modules.meetings.models import Meeting, held_on
from workmind.modules.meetings.speakers import assign_speakers, relabel
from workmind.modules.usage.calls import attribute
from workmind.modules.work.links import meeting_link
from workmind.modules.work.notifications import notify
from workmind.providers.diarization import DiarizationError, Diarizer, DisabledDiarizer, Turn
from workmind.providers.llm import LLMError, LLMProvider
from workmind.providers.transcription import Transcriber, Transcript, TranscriptionError

log = structlog.get_logger("workmind.meetings")
SECTION_SECONDS = 300  # transcript sections of 5 minutes: citations say *when* it was said


@dataclass
class MeetingDeps:
    sessions: async_sessionmaker[AsyncSession]
    storage: ObjectStorage
    llm: LLMProvider
    transcriber: Transcriber
    ingestion: Dispatcher
    diarizer: Diarizer = field(default_factory=DisabledDiarizer)


class MeetingDeletedError(RuntimeError):
    """Someone deleted the meeting while it was being processed: stop, leave nothing behind."""


def _still_there(meeting: Meeting | None) -> Meeting:
    if meeting is None:
        raise MeetingDeletedError
    return meeting


async def _status(
    deps: MeetingDeps, meeting: Meeting, status: str, error: str | None = None
) -> None:
    async with tenant_transaction(deps.sessions, meeting.tenant_id) as session:
        stored = _still_there(await session.get(Meeting, meeting.id))
        stored.status, stored.error = status, error


@dataclass(frozen=True)
class Directory:
    names: list[str]  # colleagues' display names: a spelling hint for speech to text
    people: dict[str, str]  # lower-case full or first name -> user id, unambiguous ones only


async def load_directory(session: AsyncSession) -> Directory:
    users = (
        await session.scalars(
            select(User).where(User.status == "active").order_by(User.display_name)
        )
    ).all()
    counts: dict[str, list[str]] = {}
    for user in users:
        full = (user.display_name or "").strip().lower()
        for key in {full, full.split(" ")[0]} - {""}:
            counts.setdefault(key, []).append(str(user.id))
    people = {key: ids[0] for key, ids in counts.items() if len(ids) == 1}
    return Directory([u.display_name for u in users if u.display_name], people)


def document_title(meeting: Meeting) -> str:
    """The title of the meeting's knowledge document. It is also the transcript's heading,
    which indexing reads as the title: the two must agree, or indexing renames the document."""
    day = f" ({meeting.created_at:%Y-%m-%d})" if meeting.created_at else ""
    return f"Meeting: {meeting.title}{day}"


def transcript_markdown(meeting: Meeting) -> str:
    lines = [f"# {document_title(meeting)}", ""]
    if meeting.topics:
        lines += ["Topics:", ""]
        lines += [
            f"- {clock(float(t['start']))} {relabel(str(t['title']), meeting.speakers)}"
            for t in meeting.topics
        ]
        lines.append("")
    current = -1
    for s in meeting.segments:
        section = int(float(s["start"]) // SECTION_SECONDS)
        if section != current:
            current = section
            lines += [
                "",
                f"## {clock(section * SECTION_SECONDS)} to "
                f"{clock((section + 1) * SECTION_SECONDS)}",
                "",
            ]
        name = speaker_name(meeting.speakers, s.get("speaker"))
        lines.append(f"[{clock(float(s['start']))}] {name}: {s['text']}")
    return "\n".join(lines) + "\n"


async def process_meeting(deps: MeetingDeps, tenant_id: uuid.UUID, meeting_id: uuid.UUID) -> None:
    """The extraction's model calls count for the person who uploaded the meeting. A budget
    that is used up fails the meeting, and its error says which budget and when it resets."""
    async with tenant_transaction(deps.sessions, tenant_id) as session:
        owner_id = await session.scalar(select(Meeting.owner_id).where(Meeting.id == meeting_id))
    try:
        with attribute("meetings", user_id=owner_id, ref=("meeting", meeting_id)):
            await _process(deps, tenant_id, meeting_id)
    except MeetingDeletedError:
        log.info("meeting_deleted_while_processing", meeting_id=str(meeting_id))


async def _process(deps: MeetingDeps, tenant_id: uuid.UUID, meeting_id: uuid.UUID) -> None:
    async with tenant_transaction(deps.sessions, tenant_id) as session:
        meeting = _still_there(await session.get(Meeting, meeting_id))
        tenant = await session.get(Tenant, tenant_id)
        directory = await load_directory(session)
    people = directory.people
    timings: dict[str, Any] = {}
    try:
        # 1. Words with times, and for a recording who spoke when (at the same time)
        await _status(deps, meeting, "transcribing")
        data = await deps.storage.get(owned_key(tenant_id, meeting.media_key))
        if transcripts.kind(meeting.filename) == "audio":
            transcript, turns = await asyncio.gather(
                _transcribe(deps.transcriber, data, meeting.filename, directory.names, timings),
                _diarize(deps.diarizer, data, meeting.filename, timings),
            )
            transcript.segments = assign_speakers(transcript.segments, turns)
        else:
            transcript = transcripts.parse(data, meeting.filename)
        if not transcript.segments:
            raise TranscriptionError("no speech or transcript lines found")
        segments = [
            {
                "id": f"T{i}",
                "start": round(s.start, 2),
                "end": round(s.end, 2),
                "speaker": s.speaker,
                "text": s.text,
            }
            for i, s in enumerate(transcript.segments, start=1)
        ]
        speakers = {
            label: {
                "name": label,
                "user_id": people.get(label.lower()) or people.get(label.lower().split(" ")[0]),
            }
            for label in sorted({str(s["speaker"]) for s in segments if s["speaker"]})
        }
        async with tenant_transaction(deps.sessions, tenant_id) as session:
            stored = _still_there(await session.get(Meeting, meeting_id))
            stored.segments, stored.duration_s, stored.language = (
                segments,
                transcript.duration,
                transcript.language,
            )
            stored.speakers = {**speakers, **stored.speakers}
            meeting = stored

        # 2. Outcome
        await _status(deps, meeting, "extracting")
        started = time.perf_counter()
        outcome, usage = await extract(
            deps.llm,
            segments,
            organization=tenant.name if tenant else "the company",
            day=held_on(meeting),
            speakers=meeting.speakers,
            people=people,
        )
        timings["extract_s"] = round(time.perf_counter() - started, 1)

        # 3. Topics: the model's outline checked against the transcript, else one from its words
        started = time.perf_counter()
        outline, fallback = await topics.outline(
            deps.llm,
            segments,
            organization=tenant.name if tenant else "the company",
            speakers=meeting.speakers,
        )
        timings["topics_s"] = round(time.perf_counter() - started, 1)
        if fallback:
            timings["topics_fallback"] = fallback
        meeting.topics = outline

        # 4. Searchable transcript in the meeting's collection
        document_id = await _index(deps, meeting)
        async with tenant_transaction(deps.sessions, tenant_id) as session:
            stored = _still_there(await session.get(Meeting, meeting_id))
            stored.summary, stored.topics = outcome["summary"], outline
            stored.decisions, stored.action_items = outcome["decisions"], outcome["action_items"]
            stored.document_id, stored.timings = document_id, {**timings, "model": usage.model}
            stored.status, stored.error = "ready", None
            await notify(
                session,
                tenant_id,
                stored.owner_id,
                kind="meeting.ready",
                title=f"Meeting notes ready: {stored.title}",
                link=meeting_link(stored.id),
            )
            await record_event(
                session,
                tenant_id=tenant_id,
                actor=Actor.system(),
                action="meeting.processed",
                resource_type="meeting",
                resource_id=str(meeting_id),
                details={
                    "segments": len(segments),
                    "decisions": len(outcome["decisions"]),
                    "action_items": len(outcome["action_items"]),
                    **timings,
                },
            )
        log.info("meeting_processed", meeting_id=str(meeting_id), segments=len(segments), **timings)
    except (TranscriptionError, LLMError, ValueError) as exc:
        await _status(deps, meeting, "failed", str(exc)[:500])
        log.warning("meeting_failed", meeting_id=str(meeting_id), error=str(exc)[:300])


async def _transcribe(
    transcriber: Transcriber,
    data: bytes,
    filename: str,
    vocabulary: Sequence[str],
    timings: dict[str, Any],
) -> Transcript:
    started = time.perf_counter()
    transcript = await transcriber.transcribe(data, filename, vocabulary=vocabulary)
    timings["transcribe_s"] = round(time.perf_counter() - started, 1)
    return transcript


async def _diarize(
    diarizer: Diarizer, data: bytes, filename: str, timings: dict[str, Any]
) -> list[Turn]:
    """Who spoke when. Without it the meeting still gets a transcript, unlabelled, and the
    reason is kept (`diarization_skipped`)."""
    started = time.perf_counter()
    try:
        turns = await diarizer.diarize(data, filename)
    except Exception as exc:  # off, not installed, no models, a file it cannot read
        timings["diarization_skipped"] = str(exc)[:300]
        # Expected reasons say so in DiarizationError; anything else is worth a closer look.
        report = log.info if isinstance(exc, DiarizationError) else log.warning
        report("meeting_not_diarized", reason=str(exc)[:300])
        return []
    timings["diarize_s"] = round(time.perf_counter() - started, 1)
    timings["diarizer"] = diarizer.model_id
    timings["speakers_found"] = len({t.speaker for t in turns})
    return turns


def transcript_filename(meeting: Meeting) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", meeting.title.lower()).strip("-")[:50] or "meeting"
    return f"meeting-{slug}-{str(meeting.id)[-8:]}.md"


async def store_transcript(
    session: AsyncSession, ctx: RequestContext, storage: ObjectStorage, meeting: Meeting
) -> UploadResult:
    """The transcript as a document of the meeting's collection: a new version of it when the
    transcript changed (speakers were named), nothing new when it did not. The caller holds the
    meeting locked, and dispatches the version for indexing after committing."""
    collection = await session.get(Collection, meeting.collection_id)
    assert collection is not None  # noqa: S101
    result = await service.upload(
        session,
        ctx,
        storage,
        collection,
        filename=transcript_filename(meeting),
        declared_mime="text/markdown",
        data=transcript_markdown(meeting).encode(),
        source="meeting",
        trust="discussion",
    )
    result.document.url = f"/meetings/{meeting.id}"
    result.document.title = document_title(meeting)
    meeting.document_id = result.document.id
    return result


async def _index(deps: MeetingDeps, meeting: Meeting) -> uuid.UUID:
    owner = RequestContext(
        request_id=f"meeting-{meeting.id}",
        tenant_id=meeting.tenant_id,
        user_id=meeting.owner_id,
        org_role="member",
        principals=frozenset(),
        auth_method="service",
        scopes=frozenset({"*"}),
    )
    async with tenant_transaction(deps.sessions, meeting.tenant_id) as session:
        # Locked, and the document recorded on it in this same transaction: a deletion either
        # happens first (we stop here) or waits and then finds the document to delete with it.
        stored = _still_there(await session.get(Meeting, meeting.id, with_for_update=True))
        stored.topics = meeting.topics
        result = await store_transcript(session, owner, deps.storage, stored)
    if not result.unchanged:
        await deps.ingestion.dispatch(meeting.tenant_id, result.document.id, result.version.id)
    return result.document.id
