"""Agent definitions (ARCHITECTURE section 12.2): agents are data, not code.

One parameterized department agent runs every definition. A definition says what the agent
knows about (its collections, which only *narrow* what the asking user may see), how it is
described to the planner, and its limits.
"""

import uuid

from sqlalchemy import CheckConstraint, Integer, String, Text, UniqueConstraint, text
from sqlalchemy import true as sa_true
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from workmind.core.db import Base, TenantScoped, Timestamps, uuid_primary_key
from workmind.modules.identity.models import SLUG_PATTERN


class AgentDefinition(Base, TenantScoped, Timestamps):
    __tablename__ = "agent_definitions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "key"),
        CheckConstraint(f"key ~ '{SLUG_PATTERN}'", name="key_format"),
        CheckConstraint("max_searches BETWEEN 1 AND 5", name="max_searches"),
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
