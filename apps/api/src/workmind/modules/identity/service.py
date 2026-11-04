"""Sign-in and identity loading."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from workmind.core.tenancy import global_transaction, tenant_transaction
from workmind.modules.governance.audit import Actor, record_event
from workmind.modules.identity.models import (
    Department,
    DepartmentMember,
    Group,
    GroupMember,
    Tenant,
    User,
)

SessionFactory = async_sessionmaker[AsyncSession]


class SignInError(Exception):
    """A sign-in refused for a reason the user may see (used as an error code in the UI)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Identity:
    user: User
    departments: Sequence[tuple[Department, str]]  # (department, role)
    groups: Sequence[Group]  # direct and inherited through nested groups

    @property
    def principals(self) -> frozenset[str]:
        principals = {f"user:{self.user.id}"}
        if self.user.org_role != "guest":  # guests see only what is shared with them
            principals.add(f"org:{self.user.tenant_id}")
        principals.update(f"dept:{department.id}" for department, _ in self.departments)
        principals.update(f"group:{group.id}" for group in self.groups)
        return frozenset(principals)


def organization_aliases(claim: object) -> list[str]:
    """Keycloak sends `organization` as a list of aliases, or as a map when ids are included."""
    if isinstance(claim, str):
        return [claim]
    if isinstance(claim, list):
        return [alias for alias in claim if isinstance(alias, str)]  # pyright: ignore[reportUnknownVariableType]
    if isinstance(claim, dict):
        return [alias for alias in claim if isinstance(alias, str)]  # pyright: ignore[reportUnknownVariableType]
    return []


async def resolve_tenant(sessions: SessionFactory, organization_claim: object) -> Tenant | None:
    aliases = organization_aliases(organization_claim)
    if not aliases:
        return None
    async with global_transaction(sessions) as session:
        return (
            await session.scalars(
                select(Tenant)
                .where(Tenant.slug.in_(aliases), Tenant.status == "active")
                .order_by(Tenant.slug)
            )
        ).first()


async def sign_in(
    sessions: SessionFactory,
    tenant_id: uuid.UUID,
    claims: dict[str, Any],
    *,
    ip: str | None,
    user_agent: str | None,
) -> User:
    """Link the Keycloak identity to a WorkMind user (creating one if needed) and audit it."""
    subject = str(claims["sub"])
    email = claims.get("email")
    email_verified = claims.get("email_verified") is True

    async with tenant_transaction(sessions, tenant_id) as session:
        user = (await session.scalars(select(User).where(User.keycloak_sub == subject))).first()
        created = False
        if user is None and email and email_verified:
            # Linking by email is safe only for verified addresses: otherwise anyone who can
            # set an unverified email in the identity provider could take over an account.
            user = (await session.scalars(select(User).where(User.email == email))).first()
            if user is not None and user.keycloak_sub not in (None, subject):
                raise SignInError("account_conflict")
        if user is None:
            if not (email and email_verified):
                raise SignInError("email_not_verified")
            # Just-in-time provisioning: membership of the Keycloak organization already
            # proves the person belongs to this tenant.
            user = User(
                tenant_id=tenant_id,
                email=email,
                display_name=str(claims.get("name") or email),
                org_role="member",
                status="active",
            )
            session.add(user)
            created = True
        if user.status == "disabled":
            raise SignInError("account_disabled")

        user.keycloak_sub = subject
        user.status = "active"
        user.last_login_at = datetime.now(UTC)
        await session.flush()
        await record_event(
            session,
            tenant_id=tenant_id,
            actor=Actor(type="user", id=user.id),
            action="auth.login",
            resource_type="user",
            resource_id=str(user.id),
            details={"method": "oidc", "provisioned": created},
            ip=ip,
            user_agent=user_agent,
        )
        return user


async def record_denied_sign_in(
    sessions: SessionFactory,
    tenant_id: uuid.UUID,
    *,
    reason: str,
    email: str | None,
    ip: str | None,
    user_agent: str | None,
) -> None:
    async with tenant_transaction(sessions, tenant_id) as session:
        await record_event(
            session,
            tenant_id=tenant_id,
            actor=Actor(type="user"),
            action="auth.login_denied",
            details={"reason": reason, "email": email},
            ip=ip,
            user_agent=user_agent,
        )


async def load_identity(session: AsyncSession, user_id: uuid.UUID) -> Identity | None:
    """Load a user with departments and all groups, including groups inherited through
    nested groups. Must run inside the user's tenant transaction."""
    user = await session.get(User, user_id)
    if user is None:
        return None

    department_rows = (
        await session.execute(
            select(Department, DepartmentMember.role)
            .join(DepartmentMember, DepartmentMember.department_id == Department.id)
            .where(DepartmentMember.user_id == user_id)
            .order_by(Department.name)
        )
    ).all()

    # UNION (not UNION ALL) stops the recursion even if groups ever form a cycle.
    containing = (
        select(GroupMember.group_id.label("id"))
        .where(GroupMember.member_user_id == user_id)
        .cte("containing", recursive=True)
    )
    containing = containing.union(
        select(GroupMember.group_id).join(
            containing, GroupMember.member_group_id == containing.c.id
        )
    )
    groups = (
        await session.scalars(
            select(Group).join(containing, Group.id == containing.c.id).order_by(Group.name)
        )
    ).all()

    return Identity(
        user=user,
        departments=[(department, role) for department, role in department_rows],
        groups=groups,
    )
