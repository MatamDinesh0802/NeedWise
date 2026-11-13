"""Which department a person's usage counts for (docs/usage.md, "Attribution").

A person can belong to several departments. Their usage is attributed to one: the department
they joined first (the oldest membership; on a tie, the smaller department id). The choice is
made when each event is recorded, so moving someone to another department changes where their
later usage counts, never the history.
"""

import uuid

from sqlalchemy import ScalarSelect, select
from sqlalchemy.ext.asyncio import AsyncSession

from needwise.modules.identity.models import DepartmentMember


def primary_department_query(user_id: uuid.UUID) -> ScalarSelect[uuid.UUID]:
    return (
        select(DepartmentMember.department_id)
        .where(DepartmentMember.user_id == user_id)
        .order_by(DepartmentMember.created_at, DepartmentMember.department_id)
        .limit(1)
        .scalar_subquery()
    )


async def primary_department(session: AsyncSession, user_id: uuid.UUID) -> uuid.UUID | None:
    return await session.scalar(select(primary_department_query(user_id)))
