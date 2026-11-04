"""Governance: the append-only, hash-chained audit log (ARCHITECTURE §9, §20)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, DateTime, LargeBinary, String, Text, text
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import UniqueConstraint

from workmind.core.db import Base, TenantScoped, uuid_primary_key


class AuditEvent(Base, TenantScoped):
    __tablename__ = "audit_events"
    __table_args__ = (
        # seq orders the chain per tenant; the unique constraint also serves tenant queries.
        UniqueConstraint("tenant_id", "seq"),
        CheckConstraint("actor_type IN ('user', 'service', 'system')", name="actor_type"),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    seq: Mapped[int] = mapped_column(BigInteger)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actor_type: Mapped[str] = mapped_column(String(16))
    actor_id: Mapped[uuid.UUID | None]
    action: Mapped[str] = mapped_column(Text)
    resource_type: Mapped[str | None] = mapped_column(Text)
    resource_id: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    prev_hash: Mapped[bytes | None] = mapped_column(LargeBinary)
    hash: Mapped[bytes] = mapped_column(LargeBinary)
