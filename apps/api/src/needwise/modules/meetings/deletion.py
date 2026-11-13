"""Deleting a meeting: the meeting, its recording or transcript file, and its indexed transcript
(the document, its versions, chunks and search points) go; tasks made from its action items stay
with their assignees and only lose the link to it.

The caller commits, then removes the search points and the files this returns: a storage key is
content-addressed, so it is only returned when nothing else (another meeting, a document
version) still uses it.
"""

import uuid
from dataclasses import dataclass, field

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from needwise.core.context import RequestContext
from needwise.modules.governance.audit import Actor, record_event
from needwise.modules.knowledge import service
from needwise.modules.knowledge.models import Collection, Document, DocumentVersion
from needwise.modules.meetings.models import Meeting
from needwise.modules.work.links import meeting_link
from needwise.modules.work.models import Notification, Task


@dataclass
class DeletedMeeting:
    document_id: uuid.UUID | None = None  # its points must leave the search index
    storage_keys: list[str] = field(default_factory=list[str])  # no longer used by anything
    tasks_unlinked: int = 0


async def delete_meeting(
    session: AsyncSession, ctx: RequestContext, meeting: Meeting
) -> DeletedMeeting:
    """Must run inside the tenant transaction that locked the meeting (FOR UPDATE), so processing
    still running cannot add a transcript document after this looked for it."""
    deleted = DeletedMeeting()
    collection = await session.get(Collection, meeting.collection_id)

    # Tasks stay with their assignees; they keep the meeting's title, not a link to nothing.
    tasks = (
        await session.scalars(
            select(Task).where(Task.origin["meeting_id"].astext == str(meeting.id))
        )
    ).all()
    for task in tasks:
        origin = {k: v for k, v in task.origin.items() if k not in ("meeting_id", "action_index")}
        task.origin = {**origin, "meeting_title": meeting.title, "meeting_deleted": True}
    deleted.tasks_unlinked = len(tasks)

    # "Meeting notes ready" would open a page that is gone.
    await session.execute(delete(Notification).where(Notification.link == meeting_link(meeting.id)))

    keys = {meeting.media_key}
    document = await session.get(Document, meeting.document_id) if meeting.document_id else None
    if document is not None:
        keys.update(await service.delete_document(session, ctx, document))
        deleted.document_id = document.id

    await session.delete(meeting)
    await session.flush()
    used = set(
        (await session.scalars(select(Meeting.media_key).where(Meeting.media_key.in_(keys)))).all()
    ) | set(
        (
            await session.scalars(
                select(DocumentVersion.storage_key).where(DocumentVersion.storage_key.in_(keys))
            )
        ).all()
    )
    deleted.storage_keys = sorted(keys - used)

    await record_event(
        session,
        tenant_id=ctx.tenant_id,
        actor=Actor(type="user", id=ctx.user_id),
        action="meeting.deleted",
        resource_type="meeting",
        resource_id=str(meeting.id),
        details={
            "title": meeting.title,
            "filename": meeting.filename,
            "collection": collection.key if collection else None,
            "document_id": str(deleted.document_id) if deleted.document_id else None,
            "tasks_unlinked": deleted.tasks_unlinked,
        },
    )
    return deleted
