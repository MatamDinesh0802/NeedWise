"""Conversations and messages (ARCHITECTURE sections 9, 18).

Conversations are private to the person who started them. Assistant messages keep their
citations and source references so history can be shown without searching again; showing a
source's text always goes back through the permission-checked chunks API. Research answers also
keep their steps (tool calls, and the approvals they proposed), so a reload shows what was done.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import false as sa_false
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from needwise.core.db import Base, TenantScoped, Timestamps, uuid_primary_key

ROLES = ("user", "assistant")
STATUSES = ("complete", "cancelled", "failed")
RATINGS = ("up", "down")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Conversation(Base, TenantScoped, Timestamps):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"], ["users.tenant_id", "users.id"], ondelete="CASCADE"
        ),
        Index("ix_conversations_user_recent", "tenant_id", "user_id", "last_message_at"),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    user_id: Mapped[uuid.UUID]
    title: Mapped[str] = mapped_column(Text, server_default="")
    # Rolling summary of the messages before the recent window (ARCHITECTURE 11.4).
    summary: Mapped[str] = mapped_column(Text, server_default="")
    summarized_messages: Mapped[int] = mapped_column(Integer, server_default="0")
    last_message_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Message(Base, TenantScoped, Timestamps):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["conversations.tenant_id", "conversations.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(_in("role", ROLES), name="role"),
        CheckConstraint(_in("status", STATUSES), name="status"),
        CheckConstraint(f"feedback IS NULL OR {_in('feedback', RATINGS)}", name="feedback"),
        Index("ix_messages_conversation", "tenant_id", "conversation_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    conversation_id: Mapped[uuid.UUID]
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), server_default="complete")
    run_id: Mapped[uuid.UUID | None]
    # The standalone question actually searched for (after rewriting a follow-up).
    search_query: Mapped[str | None] = mapped_column(Text)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    sources: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    # Research runs, in order: [{"tool", "ok", "error", "approval_id", "summary"}]. Names and
    # outcomes only: tool inputs and outputs (search results, drafts) are not kept here.
    tool_steps: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    usage: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    # A failed answer: why, as its `run.failed` event said it ({"code", "message"}, and for a
    # used-up budget "detail" and "budget"), so a reload says it too.
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    abstained: Mapped[bool] = mapped_column(server_default=sa_false())
    withheld: Mapped[int] = mapped_column(Integer, server_default="0")
    feedback: Mapped[str | None] = mapped_column(String(8))
    feedback_comment: Mapped[str | None] = mapped_column(Text)
