"""Who may see a task: its assignee, its creator, the members of its department queue, and, for
a request waiting in triage, the people who triage (the org admins, plus the members of the
triage department if the tenant set one). The API and workflow events both ask here."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from needwise.modules.identity.models import Department, DepartmentMember, User
from needwise.modules.work.models import Task
from needwise.modules.work.policy import load_policy

ADMIN_ROLES = ("owner", "admin")


async def my_departments(session: AsyncSession, user_id: uuid.UUID) -> list[uuid.UUID]:
    return list(
        (
            await session.scalars(
                select(DepartmentMember.department_id).where(DepartmentMember.user_id == user_id)
            )
        ).all()
    )


async def triage_department_id(session: AsyncSession) -> uuid.UUID | None:
    key = (await load_policy(session)).routing.triage_department
    if not key:
        return None
    return await session.scalar(select(Department.id).where(Department.slug == key))


async def is_triager(session: AsyncSession, user_id: uuid.UUID, org_role: str) -> bool:
    if org_role in ADMIN_ROLES:
        return True
    department = await triage_department_id(session)
    return department is not None and department in await my_departments(session, user_id)


async def triagers(session: AsyncSession) -> list[uuid.UUID]:
    """Everyone who triages: active org admins and the triage department's members."""
    people = list(
        (
            await session.scalars(
                select(User.id)
                .where(User.org_role.in_(ADMIN_ROLES), User.status == "active")
                .order_by(User.email)
            )
        ).all()
    )
    department = await triage_department_id(session)
    if department is not None:
        members = (
            await session.scalars(
                select(DepartmentMember.user_id)
                .join(User, User.id == DepartmentMember.user_id)
                .where(DepartmentMember.department_id == department, User.status == "active")
            )
        ).all()
        people += [m for m in members if m not in people]
    return people


async def can_see_task(
    session: AsyncSession, user_id: uuid.UUID, org_role: str, task: Task
) -> bool:
    if user_id in (task.assignee_id, task.created_by):
        return True
    if task.department_id is not None and task.department_id in await my_departments(
        session, user_id
    ):
        return True
    return task.triage and await is_triager(session, user_id, org_role)
