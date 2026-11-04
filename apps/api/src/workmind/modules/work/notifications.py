"""In-app notifications: approvals waiting, decisions made, tasks assigned."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

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
    session.add(
        Notification(
            tenant_id=tenant_id,
            user_id=user_id,
            kind=kind,
            title=title[:300],
            body=body[:2000],
            link=link,
        )
    )
