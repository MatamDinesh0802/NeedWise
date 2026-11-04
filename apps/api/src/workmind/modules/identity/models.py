"""Identity: tenants, users, departments and groups (ARCHITECTURE sections 6 to 9)."""

import uuid
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from workmind.core.db import Base, TenantScoped, Timestamps, uuid_primary_key

OrgRole = Literal["owner", "admin", "auditor", "member", "guest"]
UserStatus = Literal["invited", "active", "disabled"]
DepartmentRole = Literal["manager", "member"]
GroupSource = Literal["local", "idp", "connector"]

ORG_ROLES: tuple[OrgRole, ...] = ("owner", "admin", "auditor", "member", "guest")
# Slugs never contain "_": personal access tokens use it as a separator.
SLUG_PATTERN = r"^[a-z0-9]+(-[a-z0-9]+)*$"


class Tenant(Base, Timestamps):
    """An organization. Not tenant-scoped itself: login resolves it before a tenant is known.
    The runtime role may only read it (see migration 0002)."""

    __tablename__ = "tenants"
    __table_args__ = (
        CheckConstraint(f"slug ~ '{SLUG_PATTERN}'", name="slug_format"),
        CheckConstraint("status IN ('active', 'suspended')", name="status"),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    slug: Mapped[str] = mapped_column(CITEXT, unique=True)
    name: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), server_default="active")
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))


class User(Base, TenantScoped, Timestamps):
    __tablename__ = "users"
    __table_args__ = (
        # Target of composite foreign keys: a membership can only reference a user of the
        # same tenant, even though foreign-key checks ignore Row-Level Security.
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "email"),
        UniqueConstraint("tenant_id", "keycloak_sub"),
        CheckConstraint(
            "org_role IN ('owner', 'admin', 'auditor', 'member', 'guest')", name="org_role"
        ),
        CheckConstraint("status IN ('invited', 'active', 'disabled')", name="status"),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    email: Mapped[str] = mapped_column(CITEXT)
    display_name: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    # Set on first login (or by seeding); invited users don't have one yet.
    keycloak_sub: Mapped[str | None] = mapped_column(Text)
    org_role: Mapped[str] = mapped_column(String(16), server_default="member")
    status: Mapped[str] = mapped_column(String(16), server_default="active")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Department(Base, TenantScoped, Timestamps):
    __tablename__ = "departments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "slug"),
        CheckConstraint(f"slug ~ '{SLUG_PATTERN}'", name="slug_format"),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    slug: Mapped[str] = mapped_column(CITEXT)
    name: Mapped[str] = mapped_column(Text)


class DepartmentMember(Base, TenantScoped):
    __tablename__ = "department_members"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "department_id"],
            ["departments.tenant_id", "departments.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"], ["users.tenant_id", "users.id"], ondelete="CASCADE"
        ),
        CheckConstraint("role IN ('manager', 'member')", name="role"),
    )

    department_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    role: Mapped[str] = mapped_column(String(16), server_default="member")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Group(Base, TenantScoped, Timestamps):
    __tablename__ = "groups"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "slug"),
        CheckConstraint(f"slug ~ '{SLUG_PATTERN}'", name="slug_format"),
        CheckConstraint("source IN ('local', 'idp', 'connector')", name="source"),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    slug: Mapped[str] = mapped_column(CITEXT)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16), server_default="local")


class GroupMember(Base, TenantScoped):
    """A member is either a user or another group (nested groups)."""

    __tablename__ = "group_members"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "group_id"], ["groups.tenant_id", "groups.id"], ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "member_user_id"], ["users.tenant_id", "users.id"], ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "member_group_id"],
            ["groups.tenant_id", "groups.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "(member_user_id IS NULL) <> (member_group_id IS NULL)", name="exactly_one_member"
        ),
        CheckConstraint("member_group_id IS DISTINCT FROM group_id", name="not_self"),
        UniqueConstraint("group_id", "member_user_id"),
        UniqueConstraint("group_id", "member_group_id"),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    group_id: Mapped[uuid.UUID]
    member_user_id: Mapped[uuid.UUID | None]
    member_group_id: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
