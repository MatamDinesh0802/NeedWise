"""Organization administration: people, departments, groups.

Every change runs in the caller's tenant transaction and, in that same transaction:
- checks the guard rails (last owner, self-changes, owner-only grants, group cycles),
- writes an audit event,
- queues the matching OpenFGA tuple changes in the authorization outbox.
"""

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from needwise.core.context import RequestContext
from needwise.core.errors import ApiError
from needwise.modules.authz.openfga import RelationTuple
from needwise.modules.authz.sync import ORG_RELATION, enqueue, org_object
from needwise.modules.governance.audit import Actor, record_event
from needwise.modules.identity.models import (
    Department,
    DepartmentMember,
    Group,
    GroupMember,
    User,
)

OWNER_ONLY_ROLES = frozenset({"owner"})


def not_found(kind: str) -> ApiError:
    return ApiError(404, "not_found", f"{kind} not found")


def conflict(code: str, title: str, detail: str | None = None) -> ApiError:
    return ApiError(409, code, title, detail)


async def _audit(
    session: AsyncSession,
    ctx: RequestContext,
    action: str,
    resource_type: str,
    resource_id: uuid.UUID | str,
    details: dict[str, Any] | None = None,
) -> None:
    await record_event(
        session,
        tenant_id=ctx.tenant_id,
        actor=Actor(type="user", id=ctx.user_id),
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id),
        details=details,
    )


async def _flush_or_conflict(session: AsyncSession, code: str, title: str) -> None:
    try:
        await session.flush()
    except IntegrityError as exc:
        raise conflict(code, title) from exc


# --- Tuples for one user ------------------------------------------------------------------


def _org_tuple(tenant_id: uuid.UUID, user_id: uuid.UUID, org_role: str) -> list[RelationTuple]:
    relation = ORG_RELATION.get(org_role)
    return [RelationTuple(f"user:{user_id}", relation, org_object(tenant_id))] if relation else []


async def _membership_tuples(session: AsyncSession, user_id: uuid.UUID) -> list[RelationTuple]:
    tuples = [
        RelationTuple(f"user:{user_id}", m.role, f"department:{m.department_id}")
        for m in await session.scalars(
            select(DepartmentMember).where(DepartmentMember.user_id == user_id)
        )
    ]
    tuples += [
        RelationTuple(f"user:{user_id}", "member", f"group:{m.group_id}")
        for m in await session.scalars(
            select(GroupMember).where(GroupMember.member_user_id == user_id)
        )
    ]
    return tuples


# --- People -------------------------------------------------------------------------------


@dataclass(frozen=True)
class UserPage:
    users: Sequence[User]
    next_cursor: str | None


async def list_users(
    session: AsyncSession, *, query: str | None, status: str | None, limit: int, after: str | None
) -> UserPage:
    statement = select(User).order_by(User.email).limit(limit + 1)
    if query:
        pattern = f"%{query.strip()}%"
        statement = statement.where(
            or_(User.email.ilike(pattern), User.display_name.ilike(pattern))
        )
    if status:
        statement = statement.where(User.status == status)
    if after:
        statement = statement.where(User.email > after)
    users = list((await session.scalars(statement)).all())
    more = len(users) > limit
    users = users[:limit]
    return UserPage(users=users, next_cursor=users[-1].email if more else None)


async def get_user(session: AsyncSession, user_id: uuid.UUID) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise not_found("User")
    return user


def _check_role_grant(ctx: RequestContext, *roles: str) -> None:
    if OWNER_ONLY_ROLES.intersection(roles) and not ctx.has_role("owner"):
        raise ApiError(403, "owner_required", "Only an owner can grant or remove the owner role")


async def create_user(
    session: AsyncSession,
    ctx: RequestContext,
    *,
    email: str,
    display_name: str,
    title: str | None,
    org_role: str,
) -> User:
    """Pre-provision a person. They become active on their first sign-in through Keycloak
    (linked by verified email)."""
    _check_role_grant(ctx, org_role)
    user = User(
        tenant_id=ctx.tenant_id,
        email=email,
        display_name=display_name,
        title=title,
        org_role=org_role,
        status="invited",
    )
    session.add(user)
    await _flush_or_conflict(session, "email_taken", "A person with this email already exists")
    enqueue(session, ctx.tenant_id, writes=_org_tuple(ctx.tenant_id, user.id, org_role))
    await _audit(session, ctx, "admin.user.created", "user", user.id, {"org_role": org_role})
    return user


async def _active_owner_count(session: AsyncSession) -> int:
    count = await session.scalar(
        select(func.count())
        .select_from(User)
        .where(User.org_role == "owner", User.status != "disabled")
    )
    return count or 0


async def update_user(
    session: AsyncSession, ctx: RequestContext, user_id: uuid.UUID, changes: dict[str, Any]
) -> tuple[User, bool]:
    """Returns the user and whether they were just disabled (so the caller ends sessions)."""
    user = await get_user(session, user_id)
    if (
        changes.get("status") == "active"
        and user.keycloak_sub is None
        and user.last_login_at is None
    ):
        changes = {**changes, "status": "invited"}  # re-enabled, but has never signed in
    new_role = changes.get("org_role", user.org_role)
    new_status = changes.get("status", user.status)
    role_changed, status_changed = new_role != user.org_role, new_status != user.status

    if (role_changed or status_changed) and user.id == ctx.user_id:
        raise ApiError(403, "self_change", "You cannot change your own role or status")
    if role_changed:
        _check_role_grant(ctx, user.org_role, new_role)
    if status_changed:
        # Disabling or re-enabling an owner is as powerful as removing the role.
        _check_role_grant(ctx, user.org_role)
    losing_owner = user.org_role == "owner" and (new_role != "owner" or new_status == "disabled")
    if losing_owner and user.status != "disabled" and await _active_owner_count(session) <= 1:
        raise conflict("last_owner", "The organization needs at least one active owner")

    audit_changes = {
        field: [getattr(user, field), value]
        for field, value in changes.items()
        if getattr(user, field) != value
    }
    was_disabled = user.status == "disabled"
    writes: list[RelationTuple] = []
    deletes: list[RelationTuple] = []
    if new_status == "disabled" and not was_disabled:
        deletes += _org_tuple(ctx.tenant_id, user.id, user.org_role)
        deletes += await _membership_tuples(session, user.id)
    elif was_disabled and new_status != "disabled":
        writes += _org_tuple(ctx.tenant_id, user.id, new_role)
        writes += await _membership_tuples(session, user.id)
    elif role_changed and new_status != "disabled":
        deletes += _org_tuple(ctx.tenant_id, user.id, user.org_role)
        writes += _org_tuple(ctx.tenant_id, user.id, new_role)

    for field, value in changes.items():
        setattr(user, field, value)
    await session.flush()
    if writes or deletes:
        enqueue(session, ctx.tenant_id, writes=writes, deletes=deletes)
    if audit_changes:
        await _audit(
            session, ctx, "admin.user.updated", "user", user.id, {"changes": audit_changes}
        )
    return user, (new_status == "disabled" and not was_disabled)


# --- Departments --------------------------------------------------------------------------


async def get_department(session: AsyncSession, department_id: uuid.UUID) -> Department:
    department = await session.get(Department, department_id)
    if department is None:
        raise not_found("Department")
    return department


async def create_department(
    session: AsyncSession, ctx: RequestContext, *, slug: str, name: str
) -> Department:
    department = Department(tenant_id=ctx.tenant_id, slug=slug, name=name)
    session.add(department)
    await _flush_or_conflict(session, "slug_taken", "A department with this slug already exists")
    enqueue(
        session,
        ctx.tenant_id,
        writes=[
            RelationTuple(org_object(ctx.tenant_id), "organization", f"department:{department.id}")
        ],
    )
    await _audit(
        session, ctx, "admin.department.created", "department", department.id, {"slug": slug}
    )
    return department


async def rename_department(
    session: AsyncSession, ctx: RequestContext, department_id: uuid.UUID, name: str
) -> Department:
    department = await get_department(session, department_id)
    old = department.name
    department.name = name
    await session.flush()
    await _audit(
        session, ctx, "admin.department.renamed", "department", department.id, {"name": [old, name]}
    )
    return department


async def delete_department(
    session: AsyncSession, ctx: RequestContext, department_id: uuid.UUID
) -> None:
    department = await get_department(session, department_id)
    members = (
        await session.scalars(
            select(DepartmentMember).where(DepartmentMember.department_id == department.id)
        )
    ).all()
    tuples = [
        RelationTuple(org_object(ctx.tenant_id), "organization", f"department:{department.id}")
    ]
    tuples += [
        RelationTuple(f"user:{m.user_id}", m.role, f"department:{department.id}") for m in members
    ]
    await session.delete(department)
    await session.flush()
    enqueue(session, ctx.tenant_id, deletes=tuples)
    await _audit(
        session,
        ctx,
        "admin.department.deleted",
        "department",
        department_id,
        {"slug": department.slug},
    )


async def set_department_member(
    session: AsyncSession,
    ctx: RequestContext,
    department_id: uuid.UUID,
    user_id: uuid.UUID,
    role: str,
) -> DepartmentMember:
    department = await get_department(session, department_id)
    user = await get_user(session, user_id)
    member = await session.get(DepartmentMember, (department.id, user.id))
    old_role = member.role if member else None
    if member is None:
        member = DepartmentMember(
            tenant_id=ctx.tenant_id, department_id=department.id, user_id=user.id
        )
        session.add(member)
    member.role = role
    await session.flush()
    if old_role != role and user.status != "disabled":
        obj = f"department:{department.id}"
        enqueue(
            session,
            ctx.tenant_id,
            writes=[RelationTuple(f"user:{user.id}", role, obj)],
            deletes=[RelationTuple(f"user:{user.id}", old_role, obj)] if old_role else [],
        )
    await _audit(
        session,
        ctx,
        "admin.department.member_set",
        "department",
        department.id,
        {"user_id": str(user.id), "role": [old_role, role]},
    )
    return member


async def remove_department_member(
    session: AsyncSession, ctx: RequestContext, department_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    member = await session.get(DepartmentMember, (department_id, user_id))
    if member is None:
        raise not_found("Membership")
    await session.delete(member)
    await session.flush()
    enqueue(
        session,
        ctx.tenant_id,
        deletes=[RelationTuple(f"user:{user_id}", member.role, f"department:{department_id}")],
    )
    await _audit(
        session,
        ctx,
        "admin.department.member_removed",
        "department",
        department_id,
        {"user_id": str(user_id)},
    )


# --- Groups -------------------------------------------------------------------------------


async def get_group(session: AsyncSession, group_id: uuid.UUID) -> Group:
    group = await session.get(Group, group_id)
    if group is None:
        raise not_found("Group")
    return group


async def create_group(
    session: AsyncSession, ctx: RequestContext, *, slug: str, name: str, description: str | None
) -> Group:
    group = Group(tenant_id=ctx.tenant_id, slug=slug, name=name, description=description)
    session.add(group)
    await _flush_or_conflict(session, "slug_taken", "A group with this slug already exists")
    enqueue(
        session,
        ctx.tenant_id,
        writes=[RelationTuple(org_object(ctx.tenant_id), "organization", f"group:{group.id}")],
    )
    await _audit(session, ctx, "admin.group.created", "group", group.id, {"slug": slug})
    return group


def _group_member_tuple(member: GroupMember) -> RelationTuple:
    member_ref = (
        f"user:{member.member_user_id}"
        if member.member_user_id
        else f"group:{member.member_group_id}#member"
    )
    return RelationTuple(member_ref, "member", f"group:{member.group_id}")


def _require_local(group: Group) -> None:
    """Groups synced from an identity provider or a connector are managed by their source."""
    if group.source != "local":
        raise conflict(
            "managed_externally",
            "This group is managed by its source system",
            f"Change it in the {group.source} it comes from.",
        )


async def delete_group(session: AsyncSession, ctx: RequestContext, group_id: uuid.UUID) -> None:
    group = await get_group(session, group_id)
    _require_local(group)
    related = (
        await session.scalars(
            select(GroupMember).where(
                or_(GroupMember.group_id == group.id, GroupMember.member_group_id == group.id)
            )
        )
    ).all()
    tuples = [RelationTuple(org_object(ctx.tenant_id), "organization", f"group:{group.id}")]
    tuples += [_group_member_tuple(m) for m in related]
    await session.delete(group)
    await session.flush()
    enqueue(session, ctx.tenant_id, deletes=tuples)
    await _audit(session, ctx, "admin.group.deleted", "group", group_id, {"slug": group.slug})


async def _descendant_groups(session: AsyncSession, group_id: uuid.UUID) -> set[uuid.UUID]:
    """Every group nested (directly or transitively) inside `group_id`."""
    nested = (
        select(GroupMember.member_group_id.label("id"))
        .where(GroupMember.group_id == group_id, GroupMember.member_group_id.is_not(None))
        .cte("nested", recursive=True)
    )
    nested = nested.union(
        select(GroupMember.member_group_id).join(nested, GroupMember.group_id == nested.c.id)
    )
    return {gid for gid in await session.scalars(select(nested.c.id)) if gid is not None}


async def add_group_member(
    session: AsyncSession,
    ctx: RequestContext,
    group_id: uuid.UUID,
    *,
    user_id: uuid.UUID | None = None,
    child_group_id: uuid.UUID | None = None,
) -> GroupMember:
    group = await get_group(session, group_id)
    _require_local(group)
    if user_id is not None:
        user = await get_user(session, user_id)
        existing = and_(GroupMember.group_id == group.id, GroupMember.member_user_id == user.id)
        member = GroupMember(tenant_id=ctx.tenant_id, group_id=group.id, member_user_id=user.id)
        active = user.status != "disabled"
    else:
        if child_group_id is None:
            raise ApiError(422, "validation_error", "Give a user or a group to add")
        child = await get_group(session, child_group_id)
        # Nesting `child` inside `group` is a cycle if `group` already sits inside `child`.
        if child.id == group.id or group.id in await _descendant_groups(session, child.id):
            raise conflict("group_cycle", "This would make the groups contain each other")
        existing = and_(GroupMember.group_id == group.id, GroupMember.member_group_id == child.id)
        member = GroupMember(tenant_id=ctx.tenant_id, group_id=group.id, member_group_id=child.id)
        active = True

    if found := (await session.scalars(select(GroupMember).where(existing))).first():
        return found  # idempotent
    session.add(member)
    await session.flush()
    if active:
        enqueue(session, ctx.tenant_id, writes=[_group_member_tuple(member)])
    await _audit(
        session,
        ctx,
        "admin.group.member_added",
        "group",
        group.id,
        {
            "user_id": str(user_id) if user_id else None,
            "group_id": str(child_group_id) if child_group_id else None,
        },
    )
    return member


async def remove_group_member(
    session: AsyncSession,
    ctx: RequestContext,
    group_id: uuid.UUID,
    *,
    user_id: uuid.UUID | None = None,
    child_group_id: uuid.UUID | None = None,
) -> None:
    _require_local(await get_group(session, group_id))
    condition = (
        GroupMember.member_user_id == user_id
        if user_id
        else GroupMember.member_group_id == child_group_id
    )
    member = (
        await session.scalars(
            select(GroupMember).where(GroupMember.group_id == group_id, condition)
        )
    ).first()
    if member is None:
        raise not_found("Membership")
    tuple_ = _group_member_tuple(member)
    await session.execute(delete(GroupMember).where(GroupMember.id == member.id))
    enqueue(session, ctx.tenant_id, deletes=[tuple_])
    await _audit(
        session,
        ctx,
        "admin.group.member_removed",
        "group",
        group_id,
        {
            "user_id": str(user_id) if user_id else None,
            "group_id": str(child_group_id) if child_group_id else None,
        },
    )


async def count_by(
    session: AsyncSession, column: Any, ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, int]:
    rows = await session.execute(
        select(column, func.count()).where(column.in_(list(ids))).group_by(column)
    )
    return {key: count for key, count in rows}
