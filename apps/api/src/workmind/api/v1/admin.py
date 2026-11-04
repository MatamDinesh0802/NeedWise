"""Administration API (/api/v1/admin): people, departments, groups and the audit log.

Owners and admins manage the organization; auditors may only read the audit log. Organization
roles come from PostgreSQL on every request, so a revoked admin loses access immediately.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from workmind.api.deps import CurrentContext, resources_of
from workmind.core.context import RequestContext
from workmind.core.errors import ApiError
from workmind.core.tenancy import tenant_transaction
from workmind.modules.governance.audit import verify_chain
from workmind.modules.governance.models import AuditEvent
from workmind.modules.identity import admin
from workmind.modules.identity.models import (
    SLUG_PATTERN,
    Department,
    DepartmentMember,
    Group,
    GroupMember,
    User,
)

OrgRoleName = Literal["owner", "admin", "auditor", "member", "guest"]
DepartmentRoleName = Literal["manager", "member"]


def require_roles(*roles: str):
    async def dependency(ctx: CurrentContext) -> RequestContext:
        # Administration needs a browser session: no access-token scope grants it.
        if not ctx.has_scope("admin"):
            raise ApiError(403, "scope_required", "Access tokens cannot administer")
        if not ctx.has_role(*roles):
            raise ApiError(403, "forbidden", "Not allowed", f"Requires role: {', '.join(roles)}.")
        return ctx

    return Depends(dependency)


AdminContext = Annotated[RequestContext, require_roles("owner", "admin")]
AuditContext = Annotated[RequestContext, require_roles("owner", "admin", "auditor")]

router = APIRouter(prefix="/admin", tags=["admin"])


# --- Schemas ------------------------------------------------------------------------------


class MembershipRef(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    role: str | None = None


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str
    title: str | None
    org_role: str
    status: str
    last_login_at: datetime | None
    departments: list[MembershipRef]
    groups: list[MembershipRef]


class UserPageOut(BaseModel):
    items: list[UserOut]
    next_cursor: str | None


class UserCreate(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=320)
    display_name: str = Field(min_length=1, max_length=200)
    title: str | None = Field(default=None, max_length=200)
    org_role: OrgRoleName = "member"


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    title: str | None = Field(default=None, max_length=200)
    org_role: OrgRoleName | None = None
    status: Literal["active", "disabled"] | None = None


class PersonRef(BaseModel):
    id: uuid.UUID
    display_name: str


class DepartmentOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    member_count: int
    managers: list[PersonRef]


class DepartmentCreate(BaseModel):
    slug: str = Field(pattern=SLUG_PATTERN, max_length=64)
    name: str = Field(min_length=1, max_length=200)


class DepartmentRename(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class DepartmentMemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str
    role: str


class DepartmentMemberSet(BaseModel):
    role: DepartmentRoleName = "member"


class GroupOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    description: str | None
    source: str
    member_count: int


class GroupCreate(BaseModel):
    slug: str = Field(pattern=SLUG_PATTERN, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)


class GroupMemberOut(BaseModel):
    kind: Literal["user", "group"]
    id: uuid.UUID
    name: str
    email: str | None = None


class Items[T](BaseModel):
    items: list[T]


class AuditEventOut(BaseModel):
    seq: int
    occurred_at: datetime
    actor_type: str
    actor: PersonRef | None
    action: str
    resource_type: str | None
    resource_id: str | None
    details: dict[str, Any]
    ip: str | None


class AuditPageOut(BaseModel):
    items: list[AuditEventOut]
    next_before_seq: int | None


class ChainOut(BaseModel):
    ok: bool
    checked: int
    first_broken_seq: int | None


# --- Helpers ------------------------------------------------------------------------------


async def _users_out(session: Any, users: Sequence[User]) -> list[UserOut]:
    ids = [u.id for u in users]
    departments: dict[uuid.UUID, list[MembershipRef]] = {i: [] for i in ids}
    for member, department in await session.execute(
        select(DepartmentMember, Department)
        .join(Department, Department.id == DepartmentMember.department_id)
        .where(DepartmentMember.user_id.in_(ids))
        .order_by(Department.name)
    ):
        departments[member.user_id].append(
            MembershipRef(
                id=department.id, slug=department.slug, name=department.name, role=member.role
            )
        )
    groups: dict[uuid.UUID, list[MembershipRef]] = {i: [] for i in ids}
    for member, group in await session.execute(
        select(GroupMember, Group)
        .join(Group, Group.id == GroupMember.group_id)
        .where(GroupMember.member_user_id.in_(ids))
        .order_by(Group.name)
    ):
        groups[member.member_user_id].append(
            MembershipRef(id=group.id, slug=group.slug, name=group.name)
        )
    return [
        UserOut(
            id=u.id,
            email=u.email,
            display_name=u.display_name,
            title=u.title,
            org_role=u.org_role,
            status=u.status,
            last_login_at=u.last_login_at,
            departments=departments[u.id],
            groups=groups[u.id],
        )
        for u in users
    ]


# --- People -------------------------------------------------------------------------------


@router.get("/users")
async def list_users(
    ctx: AdminContext,
    request: Request,
    q: Annotated[str | None, Query(max_length=200)] = None,
    status: Literal["invited", "active", "disabled"] | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: str | None = None,
) -> UserPageOut:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        page = await admin.list_users(session, query=q, status=status, limit=limit, after=cursor)
        return UserPageOut(
            items=await _users_out(session, page.users), next_cursor=page.next_cursor
        )


@router.post("/users", status_code=201)
async def create_user(ctx: AdminContext, request: Request, body: UserCreate) -> UserOut:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        user = await admin.create_user(
            session,
            ctx,
            email=body.email,
            display_name=body.display_name,
            title=body.title,
            org_role=body.org_role,
        )
        return (await _users_out(session, [user]))[0]


@router.patch("/users/{user_id}")
async def update_user(
    ctx: AdminContext, request: Request, user_id: uuid.UUID, body: UserUpdate
) -> UserOut:
    resources = resources_of(request)
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        user, disabled = await admin.update_user(
            session, ctx, user_id, body.model_dump(exclude_unset=True, exclude_none=True)
        )
        out = (await _users_out(session, [user]))[0]
        subject = user.keycloak_sub
    if disabled and subject:
        # Requests already re-check the status; this also clears their sessions right away.
        await resources.sessions.delete_by_keycloak_subject(subject)
    return out


# --- Departments --------------------------------------------------------------------------


@router.get("/departments")
async def list_departments(ctx: AdminContext, request: Request) -> Items[DepartmentOut]:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        departments = (await session.scalars(select(Department).order_by(Department.name))).all()
        counts = await admin.count_by(
            session, DepartmentMember.department_id, [d.id for d in departments]
        )
        managers: dict[uuid.UUID, list[PersonRef]] = {d.id: [] for d in departments}
        for department_id, user_id, name in await session.execute(
            select(DepartmentMember.department_id, User.id, User.display_name)
            .join(User, User.id == DepartmentMember.user_id)
            .where(DepartmentMember.role == "manager")
            .order_by(User.display_name)
        ):
            managers.setdefault(department_id, []).append(PersonRef(id=user_id, display_name=name))
        return Items(
            items=[
                DepartmentOut(
                    id=d.id,
                    slug=d.slug,
                    name=d.name,
                    member_count=counts.get(d.id, 0),
                    managers=managers.get(d.id, []),
                )
                for d in departments
            ]
        )


@router.post("/departments", status_code=201)
async def create_department(
    ctx: AdminContext, request: Request, body: DepartmentCreate
) -> DepartmentOut:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        d = await admin.create_department(session, ctx, slug=body.slug, name=body.name)
        return DepartmentOut(id=d.id, slug=d.slug, name=d.name, member_count=0, managers=[])


@router.patch("/departments/{department_id}")
async def rename_department(
    ctx: AdminContext, request: Request, department_id: uuid.UUID, body: DepartmentRename
) -> MembershipRef:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        d = await admin.rename_department(session, ctx, department_id, body.name)
        return MembershipRef(id=d.id, slug=d.slug, name=d.name)


@router.delete("/departments/{department_id}", status_code=204)
async def delete_department(
    ctx: AdminContext, request: Request, department_id: uuid.UUID
) -> Response:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        await admin.delete_department(session, ctx, department_id)
    return Response(status_code=204)


@router.get("/departments/{department_id}/members")
async def list_department_members(
    ctx: AdminContext, request: Request, department_id: uuid.UUID
) -> Items[DepartmentMemberOut]:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        await admin.get_department(session, department_id)
        rows = await session.execute(
            select(DepartmentMember.role, User)
            .join(User, User.id == DepartmentMember.user_id)
            .where(DepartmentMember.department_id == department_id)
            .order_by(User.display_name)
        )
        return Items(
            items=[
                DepartmentMemberOut(
                    user_id=u.id, email=u.email, display_name=u.display_name, role=role
                )
                for role, u in rows
            ]
        )


@router.put("/departments/{department_id}/members/{user_id}")
async def set_department_member(
    ctx: AdminContext,
    request: Request,
    department_id: uuid.UUID,
    user_id: uuid.UUID,
    body: DepartmentMemberSet,
) -> DepartmentMemberOut:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        member = await admin.set_department_member(session, ctx, department_id, user_id, body.role)
        user = await admin.get_user(session, user_id)
        return DepartmentMemberOut(
            user_id=user.id, email=user.email, display_name=user.display_name, role=member.role
        )


@router.delete("/departments/{department_id}/members/{user_id}", status_code=204)
async def remove_department_member(
    ctx: AdminContext, request: Request, department_id: uuid.UUID, user_id: uuid.UUID
) -> Response:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        await admin.remove_department_member(session, ctx, department_id, user_id)
    return Response(status_code=204)


# --- Groups -------------------------------------------------------------------------------


def _group_out(group: Group, member_count: int) -> GroupOut:
    return GroupOut(
        id=group.id,
        slug=group.slug,
        name=group.name,
        description=group.description,
        source=group.source,
        member_count=member_count,
    )


@router.get("/groups")
async def list_groups(ctx: AdminContext, request: Request) -> Items[GroupOut]:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        groups = (await session.scalars(select(Group).order_by(Group.name))).all()
        counts = await admin.count_by(session, GroupMember.group_id, [g.id for g in groups])
        return Items(items=[_group_out(g, counts.get(g.id, 0)) for g in groups])


@router.post("/groups", status_code=201)
async def create_group(ctx: AdminContext, request: Request, body: GroupCreate) -> GroupOut:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        group = await admin.create_group(
            session, ctx, slug=body.slug, name=body.name, description=body.description
        )
        return _group_out(group, 0)


@router.delete("/groups/{group_id}", status_code=204)
async def delete_group(ctx: AdminContext, request: Request, group_id: uuid.UUID) -> Response:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        await admin.delete_group(session, ctx, group_id)
    return Response(status_code=204)


@router.get("/groups/{group_id}/members")
async def list_group_members(
    ctx: AdminContext, request: Request, group_id: uuid.UUID
) -> Items[GroupMemberOut]:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        await admin.get_group(session, group_id)
        users = await session.scalars(
            select(User)
            .join(GroupMember, GroupMember.member_user_id == User.id)
            .where(GroupMember.group_id == group_id)
            .order_by(User.display_name)
        )
        children = await session.scalars(
            select(Group)
            .join(GroupMember, GroupMember.member_group_id == Group.id)
            .where(GroupMember.group_id == group_id)
            .order_by(Group.name)
        )
        items = [GroupMemberOut(kind="group", id=g.id, name=g.name) for g in children]
        items += [
            GroupMemberOut(kind="user", id=u.id, name=u.display_name, email=u.email) for u in users
        ]
        return Items(items=items)


@router.put("/groups/{group_id}/members/users/{user_id}", status_code=204)
async def add_group_user(
    ctx: AdminContext, request: Request, group_id: uuid.UUID, user_id: uuid.UUID
) -> Response:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        await admin.add_group_member(session, ctx, group_id, user_id=user_id)
    return Response(status_code=204)


@router.put("/groups/{group_id}/members/groups/{child_id}", status_code=204)
async def add_group_child(
    ctx: AdminContext, request: Request, group_id: uuid.UUID, child_id: uuid.UUID
) -> Response:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        await admin.add_group_member(session, ctx, group_id, child_group_id=child_id)
    return Response(status_code=204)


@router.delete("/groups/{group_id}/members/users/{user_id}", status_code=204)
async def remove_group_user(
    ctx: AdminContext, request: Request, group_id: uuid.UUID, user_id: uuid.UUID
) -> Response:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        await admin.remove_group_member(session, ctx, group_id, user_id=user_id)
    return Response(status_code=204)


@router.delete("/groups/{group_id}/members/groups/{child_id}", status_code=204)
async def remove_group_child(
    ctx: AdminContext, request: Request, group_id: uuid.UUID, child_id: uuid.UUID
) -> Response:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        await admin.remove_group_member(session, ctx, group_id, child_group_id=child_id)
    return Response(status_code=204)


# --- Audit log ----------------------------------------------------------------------------


@router.get("/audit-events")
async def list_audit_events(
    ctx: AuditContext,
    request: Request,
    action: Annotated[str | None, Query(max_length=100, description="Prefix, e.g. 'auth.'")] = None,
    actor_id: uuid.UUID | None = None,
    before_seq: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> AuditPageOut:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        statement = (
            select(AuditEvent, User.display_name)
            .outerjoin(User, User.id == AuditEvent.actor_id)
            .order_by(AuditEvent.seq.desc())
            .limit(limit + 1)
        )
        if action:
            statement = statement.where(AuditEvent.action.startswith(action, autoescape=True))
        if actor_id:
            statement = statement.where(AuditEvent.actor_id == actor_id)
        if before_seq:
            statement = statement.where(AuditEvent.seq < before_seq)
        rows = (await session.execute(statement)).all()
    more = len(rows) > limit
    rows = rows[:limit]
    return AuditPageOut(
        items=[
            AuditEventOut(
                seq=event.seq,
                occurred_at=event.occurred_at,
                actor_type=event.actor_type,
                actor=PersonRef(id=event.actor_id, display_name=name)
                if event.actor_id and name
                else None,
                action=event.action,
                resource_type=event.resource_type,
                resource_id=event.resource_id,
                details=event.details,
                ip=str(event.ip) if event.ip else None,
            )
            for event, name in rows
        ],
        next_before_seq=rows[-1][0].seq if more else None,
    )


@router.get("/audit-events/verify")
async def verify_audit_chain(ctx: AuditContext, request: Request) -> ChainOut:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        result = await verify_chain(session, ctx.tenant_id)
    return ChainOut(ok=result.ok, checked=result.checked, first_broken_seq=result.first_broken_seq)
