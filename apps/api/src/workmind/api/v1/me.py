"""The signed-in user's profile, roles and memberships."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, or_, select

from workmind.api.deps import require_scope, resources_of
from workmind.core.context import RequestContext
from workmind.core.errors import ApiError, unauthenticated
from workmind.core.tenancy import tenant_transaction
from workmind.modules.identity.models import Department, DepartmentMember, Tenant, User
from workmind.modules.identity.service import load_identity

router = APIRouter(tags=["identity"])


class TenantRef(BaseModel):
    id: uuid.UUID
    slug: str
    name: str


class DepartmentRef(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    role: str


class GroupRef(BaseModel):
    id: uuid.UUID
    slug: str
    name: str


class Me(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str
    title: str | None
    org_role: str
    tenant: TenantRef
    departments: list[DepartmentRef]
    groups: list[GroupRef]


@router.get("/me")
async def me(ctx: Annotated[RequestContext, require_scope("profile:read")], request: Request) -> Me:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as db:
        identity = await load_identity(db, ctx.user_id)
        tenant = await db.get(Tenant, ctx.tenant_id)
    if identity is None or tenant is None:
        raise unauthenticated()
    user = identity.user
    return Me(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        title=user.title,
        org_role=user.org_role,
        tenant=TenantRef(id=tenant.id, slug=tenant.slug, name=tenant.name),
        departments=[
            DepartmentRef(id=d.id, slug=d.slug, name=d.name, role=role)
            for d, role in identity.departments
        ],
        groups=[GroupRef(id=g.id, slug=g.slug, name=g.name) for g in identity.groups],
    )


class Person(BaseModel):
    id: uuid.UUID
    display_name: str
    email: str
    title: str | None
    departments: list[str]  # department names


@router.get("/people")
async def people(
    ctx: Annotated[RequestContext, require_scope("profile:read")],
    request: Request,
    q: Annotated[str, Query(max_length=100)] = "",
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[Person]:
    """The colleague directory (assigning tasks, choosing approvers, mapping meeting speakers).
    Organization members only: guests see only what was shared with them, never the directory."""
    if f"org:{ctx.tenant_id}" not in ctx.principals:
        raise ApiError(403, "forbidden", "The directory is for members of the organization")
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as db:
        query = select(User).where(User.status == "active")
        if q.strip():
            pattern = f"%{q.strip().lower()}%"
            query = query.where(
                or_(
                    func.lower(User.display_name).like(pattern),
                    func.lower(User.email).like(pattern),
                )
            )
        users = (await db.scalars(query.order_by(User.display_name).limit(limit))).all()
        names: dict[uuid.UUID, list[str]] = {}
        if users:
            rows = await db.execute(
                select(DepartmentMember.user_id, Department.name)
                .join(Department, Department.id == DepartmentMember.department_id)
                .where(DepartmentMember.user_id.in_([u.id for u in users]))
                .order_by(Department.name)
            )
            for user_id, name in rows:
                names.setdefault(user_id, []).append(name)
    return [
        Person(
            id=u.id,
            display_name=u.display_name,
            email=u.email,
            title=u.title,
            departments=names.get(u.id, []),
        )
        for u in users
    ]
