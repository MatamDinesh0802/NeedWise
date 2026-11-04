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
from workmind.modules.identity.models import SLUG_PATTERN

KINDS = ("web", "github", "slack", "gdrive")
STATUSES = ("idle", "syncing", "ok", "error")


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
