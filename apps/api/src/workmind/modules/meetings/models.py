"""Meetings (ARCHITECTURE section 15): a recording or transcript becomes a summary, decisions
and action items with timestamps, and a searchable transcript in the meeting's collection
(whose access decides who can see the meeting)."""

import re
import uuid
from datetime import date
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Float,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from workmind.core.db import Base, TenantScoped, Timestamps, uuid_primary_key

STATUSES = ("pending", "transcribing", "extracting", "ready", "failed")
_FILE_DATE = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")


class Meeting(Base, TenantScoped, Timestamps):
    __tablename__ = "meetings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(
            ["tenant_id", "collection_id"],
            ["collections.tenant_id", "collections.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(["tenant_id", "owner_id"], ["users.tenant_id", "users.id"]),
        CheckConstraint(f"status IN ({', '.join(repr(s) for s in STATUSES)})", name="status"),
        Index("ix_meetings_collection", "tenant_id", "collection_id"),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    title: Mapped[str] = mapped_column(Text)
    collection_id: Mapped[uuid.UUID]
    owner_id: Mapped[uuid.UUID]
    status: Mapped[str] = mapped_column(String(16), server_default="pending")
    error: Mapped[str | None] = mapped_column(Text)
    filename: Mapped[str] = mapped_column(Text)
    media_key: Mapped[str] = mapped_column(Text)  # content-addressed object storage key
    duration_s: Mapped[float | None] = mapped_column(Float)
    language: Mapped[str | None] = mapped_column(String(16))
    # [{"id": "T1", "start": 12.3, "end": 18.0, "speaker": "Speaker 1", "text": "..."}]
    segments: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    # speaker label -> {"user_id": ..., "name": ...} (set in the speaker mapping UI)
    speakers: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    summary: Mapped[str] = mapped_column(Text, server_default="")
    # decisions: [{"text", "segment", "start"}]
    # action items: [{"text", "owner", "owner_user_id", "due", "segment", "start", "task_id"}]
    decisions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    action_items: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    document_id: Mapped[uuid.UUID | None]  # the indexed transcript
    timings: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))


def held_on(meeting: Meeting) -> date:
    """The day the meeting took place: a date in its file name (weekly-sync-2026-11-02.vtt, as
    recorders and exports name files), else the day it was uploaded. Relative dates in the
    transcript ("by Thursday") are resolved against it, and the app shows it."""
    for match in _FILE_DATE.finditer(meeting.filename):
        try:
            return date(*(int(part) for part in match.groups()))
        except ValueError:
            continue
    return meeting.created_at.date()
