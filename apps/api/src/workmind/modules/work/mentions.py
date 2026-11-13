"""Mentions: "@wei.zhang@acme.test" in a task's description tells that colleague (a "mention"
notification), if they can open the task. Someone who cannot is not told: the notification
would point at a page they cannot see, and its title would say what the task is about."""

import re
import uuid
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from workmind.modules.identity.models import User
from workmind.modules.work.links import task_link
from workmind.modules.work.models import Task
from workmind.modules.work.notifications import notify
from workmind.modules.work.visibility import can_see_task

MENTION: Final = re.compile(r"(?<![\w.@])@([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")
MAX_MENTIONS: Final = 10


def mentioned(text: str) -> list[str]:
    """The addresses mentioned in a text, lower-case, in order, at most MAX_MENTIONS."""
    found = [m.group(1).lower().rstrip(".") for m in MENTION.finditer(text or "")]
    return list(dict.fromkeys(found))[:MAX_MENTIONS]


async def notify_mentions(
    session: AsyncSession, tenant_id: uuid.UUID, task: Task, *, author: uuid.UUID
) -> list[uuid.UUID]:
    """Notifies the colleagues mentioned in the task's description who can open it (not the
    author, not the assignee: they hear about the task anyway). Returns who was told."""
    emails = mentioned(task.description)
    if not emails:
        return []
    people = (
        await session.scalars(select(User).where(User.email.in_(emails), User.status == "active"))
    ).all()
    told: list[uuid.UUID] = []
    author_name = await session.scalar(select(User.display_name).where(User.id == author))
    for person in people:
        if person.id in (author, task.assignee_id):
            continue
        if not await can_see_task(session, person.id, person.org_role, task):
            continue
        await notify(
            session,
            tenant_id,
            person.id,
            kind="mention",
            title=f"{author_name or 'A colleague'} mentioned you: {task.title}"[:300],
            link=task_link(task.id),
        )
        told.append(person.id)
    return told
