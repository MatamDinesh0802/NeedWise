"""Agent definitions as data (docs/agents.md): which tools an agent may have, snapshots of a
definition, and its numbered versions. Shared by the admin API and the seed."""

import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from workmind.modules.agents.models import AgentDefinition, AgentDefinitionVersion
from workmind.modules.tools.registry import ToolRegistry

# Built into every agent: it searches its own collections. As a tool it would search
# everything the person may read, outside the agent's area.
BUILT_IN_SEARCH = "search_knowledge"


def tool_problem(registry: ToolRegistry, name: str) -> tuple[str, str] | None:
    """Why an agent may not have this tool, as (code, message), or None if it may.

    Department agents run inside one answer and cannot pause for a person's approval, so only
    read-level tools qualify: tools that change something stay with research mode, where each
    call waits in the approvals inbox."""
    tool = registry.get(name)
    if tool is None:
        return "unknown_tool", f"No tool named {name!r} is registered."
    if name == BUILT_IN_SEARCH:
        return (
            "tool_not_allowed",
            f"{name}: searching is built in; the agent searches its own collections.",
        )
    if tool.side_effect != "read":
        return (
            "tool_not_allowed",
            f"{name} has {tool.side_effect} side effects and needs a person's approval. "
            "Department agents answer within one question and cannot wait for approvals; "
            "use research mode for actions.",
        )
    return None


def snapshot(agent: AgentDefinition) -> dict[str, Any]:
    """Everything an administrator edits, as stored in a version (and diffed in the UI)."""
    return {
        "key": agent.key,
        "name": agent.name,
        "description": agent.description,
        "instructions": agent.instructions,
        "collections": list(agent.collection_keys),
        "examples": list(agent.examples),
        "tools": list(agent.tools),
        "model": agent.model,
        "max_searches": agent.max_searches,
        "max_tool_calls": agent.max_tool_calls,
        "max_output_tokens": agent.max_output_tokens,
        "enabled": agent.enabled,
    }


def apply_fields(agent: AgentDefinition, fields: Mapping[str, Any]) -> None:
    """Sets the editable fields from an API body or a snapshot (`collections` -> keys). The
    key never changes. Fields missing from an older snapshot keep their current value."""
    agent.name = fields.get("name", agent.name)
    agent.description = fields.get("description", agent.description)
    agent.instructions = fields.get("instructions", agent.instructions)
    agent.collection_keys = list(fields.get("collections", agent.collection_keys))
    agent.examples = list(fields.get("examples", agent.examples))
    agent.tools = list(fields.get("tools", agent.tools))
    agent.model = fields.get("model", agent.model)
    agent.max_searches = fields.get("max_searches", agent.max_searches)
    agent.max_tool_calls = fields.get("max_tool_calls", agent.max_tool_calls)
    agent.max_output_tokens = fields.get("max_output_tokens", agent.max_output_tokens)
    agent.enabled = fields.get("enabled", agent.enabled)


async def latest_version(
    session: AsyncSession, agent_id: uuid.UUID
) -> AgentDefinitionVersion | None:
    return await session.scalar(
        select(AgentDefinitionVersion)
        .where(AgentDefinitionVersion.agent_id == agent_id)
        .order_by(AgentDefinitionVersion.version.desc())
        .limit(1)
    )


async def add_version(
    session: AsyncSession,
    agent: AgentDefinition,
    *,
    author_id: uuid.UUID | None,
    note: str = "",
) -> AgentDefinitionVersion:
    """Records the agent's current state as its next version (1 for a new agent) and sets
    `agent.version` to it. The caller holds the agent's row lock (or just created it)."""
    previous = await latest_version(session, agent.id)
    agent.version = previous.version + 1 if previous else 1
    version = AgentDefinitionVersion(
        tenant_id=agent.tenant_id,
        agent_id=agent.id,
        version=agent.version,
        snapshot=snapshot(agent),
        author_id=author_id,
        note=note.strip(),
    )
    session.add(version)
    await session.flush()
    return version


def changed_fields(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[str]:
    """Names of the fields that differ, in snapshot order (for audit details)."""
    names: Sequence[str] = list(dict.fromkeys([*before, *after]))
    return [n for n in names if before.get(n) != after.get(n)]
