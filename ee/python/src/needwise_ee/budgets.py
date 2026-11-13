"""Department budgets ("department_budgets"): spending limits per department.

The core stores every budget and records every usage event with the department it counts for
(the person's earliest department, needwise.modules.usage.departments). This decides what a
department budget means:

- it limits the model calls of the people whose usage counts for that department;
- owners and admins see every department's budget, everyone else the budgets of the
  departments they belong to;
- its warning goes to the department's managers and to the organization's owners and admins.
"""

import uuid
from collections.abc import Collection

from needwise.core.context import RequestContext
from needwise.modules.identity.models import Department, DepartmentMember, User
from needwise.modules.usage.departments import primary_department
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

ADMIN_ROLES = ("owner", "admin")


class DepartmentBudgets:
    """Implements needwise.modules.usage.extensions.DepartmentBudgets."""

    async def names(
        self, session: AsyncSession, ids: Collection[uuid.UUID]
    ) -> dict[uuid.UUID, str]:
        if not ids:
            return {}
        rows = await session.execute(
            select(Department.id, Department.name).where(Department.id.in_(list(ids)))
        )
        return {department_id: name for department_id, name in rows}

    async def binding(self, session: AsyncSession, user_id: uuid.UUID) -> list[uuid.UUID]:
        department = await primary_department(session, user_id)
        return [department] if department is not None else []

    async def visible(self, session: AsyncSession, ctx: RequestContext) -> list[uuid.UUID] | None:
        if ctx.has_role(*ADMIN_ROLES) and ctx.has_scope("admin"):
            return None
        rows = await session.scalars(
            select(DepartmentMember.department_id).where(DepartmentMember.user_id == ctx.user_id)
        )
        return list(rows.all())

    async def recipients(self, session: AsyncSession, department_id: uuid.UUID) -> list[uuid.UUID]:
        managers = await session.scalars(
            select(DepartmentMember.user_id)
            .join(User, User.id == DepartmentMember.user_id)
            .where(
                DepartmentMember.department_id == department_id,
                DepartmentMember.role == "manager",
                User.status == "active",
            )
        )
        admins = await session.scalars(
            select(User.id).where(User.org_role.in_(ADMIN_ROLES), User.status == "active")
        )
        return list(dict.fromkeys([*managers.all(), *admins.all()]))


def department_budgets() -> DepartmentBudgets:
    """The hook implementation: the core asks for it each time (only while licensed)."""
    return _INSTANCE


_INSTANCE = DepartmentBudgets()
