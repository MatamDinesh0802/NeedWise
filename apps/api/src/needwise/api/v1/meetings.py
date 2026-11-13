"""Meetings (ARCHITECTURE section 15). A meeting lives in a collection: whoever can view the
collection can view the meeting, and its editors can upload and correct it."""

import asyncio
import hashlib
import re
import uuid
from datetime import UTC, date, datetime, time
from pathlib import PurePath
from typing import Annotated, Any, cast

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from needwise.api.deps import rate_limit, require_scope, resources_of, settings_of
from needwise.core.context import RequestContext
from needwise.core.errors import ApiError
from needwise.core.tenancy import tenant_transaction
from needwise.modules.governance import holds
from needwise.modules.governance.audit import Actor, record_event
from needwise.modules.identity.models import User
from needwise.modules.knowledge.models import Collection
from needwise.modules.knowledge.pipeline import delete_document_points
from needwise.modules.knowledge.service import collection_access, visible_collections
from needwise.modules.knowledge.storage import object_key, owned_key
from needwise.modules.meetings import transcripts
from needwise.modules.meetings.deletion import delete_meeting
from needwise.modules.meetings.models import Meeting, held_on
from needwise.modules.meetings.pipeline import MeetingDeps, process_meeting, store_transcript
from needwise.modules.meetings.speakers import relabel, relink_owners
from needwise.modules.work.links import task_link
from needwise.modules.work.models import Task
from needwise.modules.work.notifications import notify

router = APIRouter(tags=["meetings"])
AskContext = Annotated[RequestContext, require_scope("assistant:ask")]
MEDIA_TYPES = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".mp4": "video/mp4",
    ".wav": "audio/wav",
    ".webm": "audio/webm",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".aiff": "audio/aiff",
    ".aif": "audio/aiff",
    ".mov": "video/quicktime",
}
_RANGE = re.compile(r"bytes=(\d*)-(\d*)")
ADMIN_ROLES = ("owner", "admin")


class MeetingSummary(BaseModel):
    id: uuid.UUID
    title: str
    collection_id: uuid.UUID
    status: str
    error: str | None
    duration_s: float | None
    decisions: int
    action_items: int
    held_on: date  # the day of the meeting (from the file name), else of the upload
    created_at: datetime


class MeetingOut(BaseModel):
    id: uuid.UUID
    title: str
    collection_id: uuid.UUID
    owner_id: uuid.UUID
    status: str
    error: str | None
    filename: str
    has_media: bool
    # Set when a retention policy deleted the recording (the transcript and notes stay).
    media_deleted_at: datetime | None
    duration_s: float | None
    language: str | None
    segments: list[dict[str, Any]]
    speakers: dict[str, Any]
    summary: str
    decisions: list[dict[str, Any]]
    action_items: list[dict[str, Any]]
    # [{"title", "segment", "start", "end", "source": "model" | "words"}]: what the meeting
    # talked about, in order, with the transcript's own times.
    topics: list[dict[str, Any]]
    document_id: uuid.UUID | None
    timings: dict[str, Any]
    can_edit: bool
    # Naming speakers: the uploader, attendees (a speaker mapped to them) who can edit, admins.
    can_map_speakers: bool
    held_on: date
    created_at: datetime


class Guest(BaseModel):
    """Someone outside the directory: shown by this name, linked to nobody."""

    name: str = Field(min_length=1, max_length=100)


class SpeakerMap(BaseModel):
    speakers: dict[str, uuid.UUID | Guest | None] = Field(
        description='speaker label -> a colleague\'s id, a guest ({"name": "..."}), or null to '
        "leave it unnamed"
    )


class ActionEdit(BaseModel):
    """Corrections to an extracted action item. Fields left out stay as they are."""

    text: str | None = Field(default=None, min_length=1, max_length=500)
    owner_user_id: uuid.UUID | None = None  # null: nobody
    due: date | None = None  # null: no deadline


class Commitment(BaseModel):
    meeting_id: uuid.UUID
    meeting_title: str
    meeting_date: date
    index: int
    text: str
    due: str | None
    start: float | None
    task_id: str | None
    task_status: str | None


class UnsatisfiableRangeError(ValueError):
    pass


def byte_range(header: str | None, size: int) -> tuple[int, int] | None:
    """The one byte range a media player asked for (`Range: bytes=...`), as (first, last),
    both inclusive. None means "send the whole file": no header, several ranges, or a range we
    don't understand (RFC 9110 lets a server ignore those). Raises UnsatisfiableRangeError when the
    range starts past the end."""
    match = _RANGE.fullmatch((header or "").strip())
    if match is None or match.groups() == ("", ""):
        return None
    first, last = match.groups()
    if not first:  # a suffix: the last N bytes
        if int(last) == 0 or size == 0:
            raise UnsatisfiableRangeError(header)
        return max(0, size - int(last)), size - 1
    start = int(first)
    if last and int(last) < start:
        return None
    if start >= size:
        raise UnsatisfiableRangeError(header)
    return start, min(int(last), size - 1) if last else size - 1


def _summary(m: Meeting) -> MeetingSummary:
    return MeetingSummary(
        id=m.id,
        title=m.title,
        collection_id=m.collection_id,
        status=m.status,
        error=m.error,
        duration_s=m.duration_s,
        decisions=len(m.decisions),
        action_items=len(m.action_items),
        held_on=held_on(m),
        created_at=m.created_at,
    )


def _shown(m: Meeting) -> dict[str, Any]:
    """The extraction as people read it: a diarization label the model wrote (SPEAKER_2) shows
    the name it is mapped to. Stored as extracted, so a later mapping can change it again."""
    speakers = m.speakers

    def named(items: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
        return [{**item, key: relabel(str(item.get(key) or ""), speakers)} for item in items]

    return {
        "summary": relabel(m.summary, speakers),
        "decisions": named(m.decisions, "text"),
        "action_items": named(m.action_items, "text"),
        "topics": named(m.topics, "title"),
    }


def _can_map(ctx: RequestContext, m: Meeting, can_edit: bool) -> bool:
    """Who may say who is who: the person who uploaded the meeting (they were there, or know
    who was), an attendee (a colleague a speaker is mapped to) who can edit the meeting, or an
    admin who can see it. Not every editor of the collection: naming a voice puts words in
    someone's mouth."""
    if m.owner_id == ctx.user_id:
        return True
    if ctx.has_role(*ADMIN_ROLES) and ctx.has_scope("admin"):
        return True
    attended = any(_user_of(entry) == str(ctx.user_id) for entry in m.speakers.values())
    return can_edit and attended


def _user_of(entry: object) -> str | None:
    """The colleague a speaker entry is mapped to, if any."""
    value = cast(dict[str, Any], entry).get("user_id") if isinstance(entry, dict) else None
    return value if isinstance(value, str) else None


def _out(m: Meeting, can_edit: bool, ctx: RequestContext) -> MeetingOut:
    return MeetingOut(
        id=m.id,
        title=m.title,
        collection_id=m.collection_id,
        owner_id=m.owner_id,
        status=m.status,
        error=m.error,
        filename=m.filename,
        has_media=transcripts.kind(m.filename) == "audio" and m.media_deleted_at is None,
        media_deleted_at=m.media_deleted_at,
        duration_s=m.duration_s,
        language=m.language,
        segments=m.segments,
        speakers=m.speakers,
        **_shown(m),
        document_id=m.document_id,
        timings=m.timings,
        can_edit=can_edit,
        can_map_speakers=_can_map(ctx, m, can_edit),
        held_on=held_on(m),
        created_at=m.created_at,
    )


async def _meeting(
    session: AsyncSession, ctx: RequestContext, meeting_id: uuid.UUID, *, edit: bool = False
) -> tuple[Meeting, bool]:
    meeting = await session.get(Meeting, meeting_id)
    if meeting is None:
        raise ApiError(404, "not_found", "Meeting not found")
    try:
        access = await collection_access(session, ctx, meeting.collection_id, edit=edit)
    except ApiError as exc:
        if exc.status == 404:
            raise ApiError(404, "not_found", "Meeting not found") from exc
        raise
    return meeting, access.can_edit or meeting.owner_id == ctx.user_id


@router.post("/meetings", status_code=202, dependencies=[rate_limit("ingest")])
async def upload_meeting(
    ctx: AskContext,
    request: Request,
    collection: Annotated[str, Query(description="key of the collection the meeting belongs to")],
    filename: Annotated[str, Query(min_length=1, max_length=255)],
    title: Annotated[str | None, Query(max_length=200)] = None,
) -> MeetingSummary:
    """The raw recording (mp3, m4a, wav, mp4, ...) or transcript (vtt, srt, txt) as the body."""
    resources, settings = resources_of(request), settings_of(request)
    name = PurePath(filename).name
    try:
        transcripts.kind(name)
    except ValueError as exc:
        raise ApiError(
            415, "unsupported_type", "Upload a recording or a transcript", str(exc)
        ) from exc
    data = await request.body()
    if not data:
        raise ApiError(422, "empty_file", "The file is empty")
    if len(data) > settings.max_meeting_bytes:
        raise ApiError(413, "too_large", "The recording is too large")
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        target = await session.scalar(select(Collection).where(Collection.key == collection))
        if target is None:
            raise ApiError(404, "not_found", "Collection not found")
        await collection_access(session, ctx, target.id, edit=True)
        key = object_key(ctx.tenant_id, hashlib.sha256(data).hexdigest())
        await resources.storage.put(key, data)
        meeting = Meeting(
            tenant_id=ctx.tenant_id,
            title=(title or PurePath(name).stem).strip()[:200],
            collection_id=target.id,
            owner_id=ctx.user_id,
            filename=name,
            media_key=key,
        )
        session.add(meeting)
        await session.flush()
        await record_event(
            session,
            tenant_id=ctx.tenant_id,
            actor=Actor(type="user", id=ctx.user_id),
            action="meeting.uploaded",
            resource_type="meeting",
            resource_id=str(meeting.id),
            details={"filename": name, "bytes": len(data), "collection": collection},
        )
        summary = _summary(meeting)
    if settings.ingestion_mode == "celery":
        from needwise.workers.celery_app import MEETING_TASK, app

        args = [str(ctx.tenant_id), str(summary.id)]
        await asyncio.to_thread(app.send_task, MEETING_TASK, args=args)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]  (Celery is untyped)
    else:
        deps = MeetingDeps(
            resources.db,
            resources.storage,
            await resources.models.for_tenant(ctx.tenant_id),
            resources.transcriber,
            resources.ingestion,
            resources.diarizer,
            start_runs=resources.start_workflow_runs,
        )
        resources.runs.start(_process(deps, ctx.tenant_id, summary.id))
    return summary


async def _process(deps: MeetingDeps, tenant_id: uuid.UUID, meeting_id: uuid.UUID) -> None:
    await process_meeting(deps, tenant_id, meeting_id)  # starts its workflow runs itself


@router.get("/meetings")
async def list_meetings(ctx: AskContext, request: Request) -> list[MeetingSummary]:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        visible = [access.collection.id for access, _ in await visible_collections(session, ctx)]
        rows = (
            await session.scalars(
                select(Meeting)
                .where(Meeting.collection_id.in_(visible))
                .order_by(Meeting.created_at.desc())
                .limit(200)
            )
        ).all()
    return [_summary(m) for m in rows]


@router.get("/meetings/{meeting_id}")
async def get_meeting(ctx: AskContext, request: Request, meeting_id: uuid.UUID) -> MeetingOut:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        meeting, can_edit = await _meeting(session, ctx, meeting_id)
    return _out(meeting, can_edit, ctx)


@router.delete("/meetings/{meeting_id}", status_code=204)
async def remove_meeting(ctx: AskContext, request: Request, meeting_id: uuid.UUID) -> Response:
    """Deletes the meeting, its recording or transcript file, and its indexed transcript (search
    included). For the person who uploaded it and the collection's editors. Tasks made from its
    action items stay with their assignees, without the link. A meeting under a legal hold is
    refused (409 legal_hold) and the attempt is audited."""
    resources = resources_of(request)
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        meeting, can_edit = await _meeting(session, ctx, meeting_id)
        if not can_edit:
            raise ApiError(
                403,
                "forbidden",
                "Only the person who uploaded it or the collection's editors can delete it",
            )
        await session.refresh(meeting, with_for_update=True)  # processing may still be running
        blocking = await holds.covering(session, "meeting", meeting)
        deleted = None if blocking else await delete_meeting(session, ctx, meeting)
    if deleted is None:
        raise await holds.refuse(
            resources.db,
            ctx.tenant_id,
            actor=Actor(type="user", id=ctx.user_id),
            kind="meeting",
            resource_id=meeting_id,
            holds=blocking,
        )
    if deleted.document_id is not None:
        await delete_document_points(
            resources.qdrant, resources.collection_name, ctx.tenant_id, deleted.document_id
        )
    for key in deleted.storage_keys:
        await resources.storage.delete(owned_key(ctx.tenant_id, key))
    return Response(status_code=204)


@router.get("/meetings/{meeting_id}/media")
async def meeting_media(ctx: AskContext, request: Request, meeting_id: uuid.UUID) -> Response:
    """The original recording, for playback at a timestamp. Answers Range requests with 206:
    browsers only let a player seek (jump to a timestamp) in media served that way."""
    resources = resources_of(request)
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        meeting, _ = await _meeting(session, ctx, meeting_id)
    media_type = MEDIA_TYPES.get(PurePath(meeting.filename).suffix.lower())
    if media_type is None:
        raise ApiError(404, "not_found", "This meeting has no recording")
    if meeting.media_deleted_at is not None:
        raise ApiError(
            410,
            "media_deleted",
            "The recording was deleted",
            "The retention policy deleted it; the transcript and notes are kept.",
        )
    data = await resources.storage.get(owned_key(ctx.tenant_id, meeting.media_key))
    headers = {"Content-Security-Policy": "sandbox", "Accept-Ranges": "bytes"}
    try:
        span = byte_range(request.headers.get("range"), len(data))
    except UnsatisfiableRangeError:
        return Response(
            status_code=416, headers={**headers, "Content-Range": f"bytes */{len(data)}"}
        )
    if span is None:
        return Response(content=data, media_type=media_type, headers=headers)
    first, last = span
    return Response(
        content=data[first : last + 1],
        status_code=206,
        media_type=media_type,
        headers={**headers, "Content-Range": f"bytes {first}-{last}/{len(data)}"},
    )


@router.put("/meetings/{meeting_id}/speakers")
async def map_speakers(
    ctx: AskContext, request: Request, meeting_id: uuid.UUID, body: SpeakerMap
) -> MeetingOut:
    """Who is who: names transcript speakers (SPEAKER_2, or a name the transcript gave) as
    colleagues or guests. The transcript shows the names, its indexed copy is rewritten with them
    (a new version of the transcript document), and action items owned by a renamed speaker
    name that colleague or guest. For the uploader, attendees who can edit, and admins."""
    resources = resources_of(request)
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        meeting, can_edit = await _meeting(session, ctx, meeting_id)
        if _can_map(ctx, meeting, can_edit):  # then lock: one mapping (or deletion) at a time
            await session.refresh(meeting, with_for_update=True)
        if not _can_map(ctx, meeting, can_edit):  # asked again of the locked row
            raise ApiError(
                403,
                "forbidden",
                "Only the person who uploaded it, attendees who can edit it, or an admin can "
                "name its speakers",
            )
        if meeting.status != "ready":
            raise ApiError(409, "not_ready", "Name the speakers once the meeting is processed")
        speakers = dict(meeting.speakers)
        previous = {label: str(v.get("name") or "") for label, v in speakers.items()}
        changed: dict[str, dict[str, Any]] = {}
        for label, choice in body.speakers.items():
            if label not in speakers:
                raise ApiError(422, "unknown_speaker", f"No speaker {label!r} in this meeting")
            if isinstance(choice, uuid.UUID):
                user = await session.get(User, choice)
                if user is None or user.status != "active":
                    raise ApiError(422, "unknown_user", "Unknown colleague")
                entry: dict[str, Any] = {"name": user.display_name, "user_id": str(user.id)}
            elif isinstance(choice, Guest):
                name = " ".join(choice.name.split())
                if not name:
                    raise ApiError(422, "empty_name", "Type the guest's name")
                entry = {"name": name, "user_id": None, "guest": True}
            else:
                entry = {"name": label, "user_id": None}
            if entry != speakers[label]:
                speakers[label] = changed[label] = entry
        if not changed:
            return _out(meeting, can_edit, ctx)
        items, relinked = relink_owners(meeting.action_items, changed, previous)
        meeting.speakers, meeting.action_items = speakers, items
        upload = await store_transcript(session, ctx, resources.storage, meeting)
        await record_event(
            session,
            tenant_id=ctx.tenant_id,
            actor=Actor(type="user", id=ctx.user_id),
            action="meeting.speakers_mapped",
            resource_type="meeting",
            resource_id=str(meeting.id),
            details={
                # Colleagues by id; a guest's name is personal data, so only that it was a guest.
                "speakers": {
                    label: "guest" if entry.get("guest") else entry["user_id"]
                    for label, entry in changed.items()
                },
                "action_items_relinked": relinked,
                "transcript_version": upload.version.version_no,
            },
        )
        out = _out(meeting, can_edit, ctx)
    if not upload.unchanged:
        await resources.ingestion.dispatch(ctx.tenant_id, upload.document.id, upload.version.id)
    return out


@router.patch("/meetings/{meeting_id}/actions/{index}")
async def edit_action(
    ctx: AskContext, request: Request, meeting_id: uuid.UUID, index: int, body: ActionEdit
) -> MeetingOut:
    """Fixes what extraction got wrong (the owner, most often) before it becomes a task."""
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        meeting, can_edit = await _meeting(session, ctx, meeting_id)
        if not can_edit:
            raise ApiError(
                403, "forbidden", "Only the meeting's owner or the collection's editors can do this"
            )
        if not 0 <= index < len(meeting.action_items):
            raise ApiError(404, "not_found", "No such action item")
        item = dict(meeting.action_items[index])
        if item.get("task_id"):
            raise ApiError(409, "already_a_task", "This is a task now: change the task instead")
        changed = body.model_fields_set
        if "text" in changed and body.text is not None:
            item["text"] = body.text.strip()
        if "owner_user_id" in changed:
            owner = await session.get(User, body.owner_user_id) if body.owner_user_id else None
            if body.owner_user_id and owner is None:
                raise ApiError(422, "unknown_user", "Unknown colleague")
            item["owner_user_id"] = str(owner.id) if owner else None
            item["owner"] = owner.display_name if owner else ""
        if "due" in changed:
            item["due"] = body.due.isoformat() if body.due else None
        items = list(meeting.action_items)
        items[index] = item
        meeting.action_items = items
        await record_event(
            session,
            tenant_id=ctx.tenant_id,
            actor=Actor(type="user", id=ctx.user_id),
            action="meeting.action_edited",
            resource_type="meeting",
            resource_id=str(meeting.id),
            details={"index": index, "fields": sorted(changed)},
        )
        return _out(meeting, can_edit, ctx)


@router.post("/meetings/{meeting_id}/actions/{index}/task", status_code=201)
async def action_to_task(
    ctx: AskContext, request: Request, meeting_id: uuid.UUID, index: int
) -> MeetingOut:
    """Turns an action item into a tracked task for its owner (an explicit click: no approval)."""
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        meeting, can_edit = await _meeting(session, ctx, meeting_id)
        if not can_edit:
            raise ApiError(
                403, "forbidden", "Only the meeting's owner or the collection's editors can do this"
            )
        if not 0 <= index < len(meeting.action_items):
            raise ApiError(404, "not_found", "No such action item")
        item = dict(meeting.action_items[index])
        if item.get("task_id"):
            raise ApiError(409, "already_a_task", "This action item is already a task")
        assignee = uuid.UUID(item["owner_user_id"]) if item.get("owner_user_id") else None
        due = item.get("due")
        text = relabel(item["text"], meeting.speakers)  # as it is shown
        task = Task(
            tenant_id=ctx.tenant_id,
            title=text[:200],
            description=f"From the meeting “{meeting.title}”"
            + (
                f" at {int(item['start'] // 60)}:{int(item['start'] % 60):02d}"
                if item.get("start") is not None
                else ""
            ),
            assignee_id=assignee,
            created_by=ctx.user_id,
            due_at=datetime.combine(date.fromisoformat(due), time(17, 0), UTC) if due else None,
            origin={"via": "meeting", "meeting_id": str(meeting.id), "action_index": index},
        )
        session.add(task)
        await session.flush()
        item["task_id"] = str(task.id)
        items = list(meeting.action_items)
        items[index] = item
        meeting.action_items = items
        if assignee and assignee != ctx.user_id:
            await notify(
                session,
                ctx.tenant_id,
                assignee,
                kind="task.assigned",
                title=f"New task from {meeting.title}: {text[:120]}",
                link=task_link(task.id),
            )
        await record_event(
            session,
            tenant_id=ctx.tenant_id,
            actor=Actor(type="user", id=ctx.user_id),
            action="task.created",
            resource_type="task",
            resource_id=str(task.id),
            details={
                "meeting_id": str(meeting.id),
                "assignee": str(assignee) if assignee else None,
            },
        )
        return _out(meeting, can_edit, ctx)


@router.get("/me/commitments")
async def my_commitments(ctx: AskContext, request: Request) -> list[Commitment]:
    """Action items assigned to me in meetings I can see, with their task's status."""
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        visible = [access.collection.id for access, _ in await visible_collections(session, ctx)]
        meetings = (
            await session.scalars(
                select(Meeting)
                .where(Meeting.collection_id.in_(visible), Meeting.status == "ready")
                .order_by(Meeting.created_at.desc())
            )
        ).all()
        task_ids = [
            uuid.UUID(i["task_id"])
            for m in meetings
            for i in m.action_items
            if i.get("owner_user_id") == str(ctx.user_id) and i.get("task_id")
        ]
        statuses = (
            {
                str(t.id): t.status
                for t in await session.scalars(select(Task).where(Task.id.in_(task_ids)))
            }
            if task_ids
            else {}
        )
    return [
        Commitment(
            meeting_id=m.id,
            meeting_title=m.title,
            meeting_date=held_on(m),
            index=i,
            text=relabel(item["text"], m.speakers),
            due=item.get("due"),
            start=item.get("start"),
            task_id=item.get("task_id"),
            task_status=statuses.get(item.get("task_id") or ""),
        )
        for m in meetings
        for i, item in enumerate(m.action_items)
        if item.get("owner_user_id") == str(ctx.user_id)
    ]
