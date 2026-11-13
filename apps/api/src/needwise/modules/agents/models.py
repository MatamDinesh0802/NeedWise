"""Agent definitions (ARCHITECTURE section 12.2): agents are data, not code.

One parameterized department agent runs every definition. A definition says what the agent
knows about (its collections, which only *narrow* what the asking user may see), how it is
described to the planner, which read-only tools it may call, which model it uses, and its
limits. Every change is kept as a numbered version (docs/agents.md).
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import true as sa_true
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from needwise.core.db import Base, TenantScoped, Timestamps, uuid_primary_key
from needwise.modules.identity.models import SLUG_PATTERN

MAX_SEARCHES = (1, 5)
MAX_TOOL_CALLS = (0, 5)
# Per model call of the agent (extracting findings, one turn of the tool loop). Thinking counts.
MAX_OUTPUT_TOKENS = (1000, 16000)
DEFAULT_OUTPUT_TOKENS = 6000


class AgentDefinition(Base, TenantScoped, Timestamps):
    __tablename__ = "agent_definitions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "key"),
        CheckConstraint(f"key ~ '{SLUG_PATTERN}'", name="key_format"),
        CheckConstraint("max_searches BETWEEN 1 AND 5", name="max_searches"),
        CheckConstraint("max_tool_calls BETWEEN 0 AND 5", name="max_tool_calls"),
        CheckConstraint("max_output_tokens BETWEEN 1000 AND 16000", name="max_output_tokens"),
        CheckConstraint("version >= 1", name="version"),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    key: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    # Shown to the planner: what this agent can answer. Write it for routing.
    description: Mapped[str] = mapped_column(Text)
    instructions: Mapped[str] = mapped_column(Text, server_default="")
    collection_keys: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=text("'{}'::text[]")
    )
    # Example questions, used by the fast-path router (embedding similarity).
    examples: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'::text[]"))
    max_searches: Mapped[int] = mapped_column(Integer, server_default="2")
    enabled: Mapped[bool] = mapped_column(server_default=sa_true())
    model_role: Mapped[str] = mapped_column(String(16), server_default="agent")
    # Read-level tools from the registry the agent may call besides searching. They run as the
    # person asking, with that person's permissions.
    tools: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'::text[]"))
    # A model catalog id ("system:<model>" or "<provider id>:<model>"); None: the role's model.
    model: Mapped[str | None] = mapped_column(Text)
    max_tool_calls: Mapped[int] = mapped_column(Integer, server_default="0")
    max_output_tokens: Mapped[int] = mapped_column(
        Integer, server_default=str(DEFAULT_OUTPUT_TOKENS)
    )
    version: Mapped[int] = mapped_column(Integer, server_default="1")


class AgentDefinitionVersion(Base, TenantScoped):
    """One saved state of a definition. Create, update and restore each add one; restoring
    copies an old snapshot into a new version, so history only ever grows."""

    __tablename__ = "agent_definition_versions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("agent_id", "version"),
        ForeignKeyConstraint(
            ["tenant_id", "agent_id"],
            ["agent_definitions.tenant_id", "agent_definitions.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "author_id"],
            ["users.tenant_id", "users.id"],
            ondelete="SET NULL (author_id)",
        ),
        CheckConstraint("version >= 1", name="version"),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    agent_id: Mapped[uuid.UUID]
    version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    author_id: Mapped[uuid.UUID | None]  # None: the seed, or a deleted user
    note: Mapped[str] = mapped_column(Text, server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
