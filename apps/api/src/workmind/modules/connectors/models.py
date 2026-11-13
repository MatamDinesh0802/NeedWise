"""Sources: where documents come from besides uploads (ARCHITECTURE section 10).

A source syncs into exactly one collection, so its documents get that collection's access rules
(the community permission mapping: "this Drive folder -> Finance"). Credentials are stored
encrypted (AES-GCM, `SECRETS_KEY`) and never returned by the API.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import true as sa_true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from workmind.core.db import Base, TenantScoped, Timestamps, uuid_primary_key
from workmind.modules.identity.models import PRINCIPAL_PATTERN, SLUG_PATTERN

KINDS = ("web", "github", "slack", "gdrive")
STATUSES = ("idle", "syncing", "ok", "error")
# A principal as a source names it (docs/permission-sync.md), always lower case:
#   email:<address>        a person: matched to the WorkMind user with that e-mail on its own
#   group:<address or id>  a group in the source (a Google group, a Slack user group)
#   domain:<domain>        everyone in a domain (Drive "anyone at acme.test")
#   anyone                 anyone with the link: matched to the organization on its own
#   slack-user:<id>        a Slack member whose e-mail Slack did not give
#   github-user:<login>, github-team:<org>/<slug>, github-org:<org>
EXTERNAL_PATTERN = (
    r"^(anyone|(email|group|domain|slack-user|github-user|github-team|github-org):[^\s:]{1,320})$"
)


class Source(Base, TenantScoped, Timestamps):
    __tablename__ = "sources"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "key"),
        ForeignKeyConstraint(
            ["tenant_id", "collection_id"],
            ["collections.tenant_id", "collections.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(["tenant_id", "owner_id"], ["users.tenant_id", "users.id"]),
        CheckConstraint(f"key ~ '{SLUG_PATTERN}'", name="key_format"),
        CheckConstraint(f"kind IN ({', '.join(repr(k) for k in KINDS)})", name="kind"),
        CheckConstraint(f"status IN ({', '.join(repr(s) for s in STATUSES)})", name="status"),
        CheckConstraint("interval_minutes >= 5", name="interval"),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    key: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16))
    collection_id: Mapped[uuid.UUID]
    owner_id: Mapped[uuid.UUID]  # syncs run on their behalf (audit actor, created_by)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    credentials: Mapped[bytes | None] = mapped_column(LargeBinary)  # AES-GCM ciphertext
    interval_minutes: Mapped[int] = mapped_column(Integer, server_default="60")
    enabled: Mapped[bool] = mapped_column(server_default=sa_true())
    status: Mapped[str] = mapped_column(String(16), server_default="idle")
    cursor: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))


class IdentityMapping(Base, TenantScoped, Timestamps):
    """Who a principal named by a source is in WorkMind (docs/permission-sync.md). People are
    matched by e-mail without one, and "anyone" is the organization; a group, a domain, a Slack
    or GitHub account needs a mapping, made by an administrator. A source principal that maps
    to nobody grants nothing (fail closed)."""

    __tablename__ = "identity_mappings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "external"),
        ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["users.tenant_id", "users.id"],
            ondelete="SET NULL (created_by)",
        ),
        CheckConstraint(f"external ~ '{EXTERNAL_PATTERN}'", name="external_format"),
        CheckConstraint(f"principal ~ '{PRINCIPAL_PATTERN}'", name="principal_format"),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    external: Mapped[str] = mapped_column(Text)
    principal: Mapped[str] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None]
