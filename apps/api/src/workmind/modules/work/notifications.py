"""Notifications: approvals waiting, decisions made, tasks assigned, escalations, reviews due,
budget warnings, mentions. Always in the app; by email or Slack as each person chose
(delivery.py), sent after the action's transaction commits, never inside it."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from workmind.modules.work.delivery import enqueue
from workmind.modules.work.models import Notification


async def notify(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    kind: str,
    title: str,
    body: str = "",
    link: str | None = None,
) -> None:
    notification = Notification(
        tenant_id=tenant_id,
        user_id=user_id,
        kind=kind,
        title=title[:300],
        body=body[:2000],
        link=link,
    )
    session.add(notification)
    await session.flush()  # the outbox rows reference it
    await enqueue(session, notification)
