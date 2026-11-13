"""Notifications: approvals waiting, decisions made, tasks assigned, escalations, reviews due,
budget warnings, mentions. Always in the app; by email or Slack as each person chose
(delivery.py), sent after the action's transaction commits, never inside it."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from needwise.modules.work.delivery import enqueue
from needwise.modules.work.models import Notification


async def notify(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    kind: str,
    title: str,
    body: str = "",
    link: str | None = None,
    thing: str | None = None,
) -> None:
    """`thing`: what the notification is about, when its link does not say (a page shared by
    many things, like Admin > Legal holds): a tenant's Slack channel gets one post per kind and
    thing, however many people were told."""
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
    await enqueue(session, notification, thing=thing)
