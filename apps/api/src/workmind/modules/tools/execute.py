"""The only way a tool runs (ARCHITECTURE section 13.2).

    validate input -> check the caller's scope -> gate side effects -> run -> sanitize -> audit

Write and external tools never run on a model's say-so alone: without an explicit approval they
stop with `approval_required` (the approvals inbox arrives in Phase 8).
"""

import time
from typing import Any

import structlog
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from workmind.core.context import RequestContext
from workmind.core.tenancy import tenant_transaction
from workmind.modules.governance.audit import Actor, record_event
from workmind.modules.tools.registry import ToolError, ToolRegistry, ToolResult, sanitize

log = structlog.get_logger("workmind.tools")


async def execute_tool(
    registry: ToolRegistry,
    sessions: async_sessionmaker[AsyncSession],
    ctx: RequestContext,
    name: str,
    arguments: dict[str, Any],
    *,
    approved: bool = False,
    caller: str = "agent",
) -> ToolResult:
    tool = registry.get(name)
    started = time.perf_counter()
    status, error_code = "ok", None
    try:
        if tool is None:
            raise ToolError("unknown_tool", f"No tool named {name!r}")
        try:
            parsed = tool.validate(arguments)
        except ValidationError as exc:
            raise ToolError("invalid_input", str(exc.errors(include_url=False))[:500]) from exc
        except ValueError as exc:
            raise ToolError("invalid_input", str(exc)[:500]) from exc
        if not ctx.has_scope(tool.scope):
            raise ToolError("forbidden", f"This token lacks the {tool.scope!r} scope")
        if tool.side_effect != "read" and not approved:
            raise ToolError(
                "approval_required",
                f"{tool.name} has {tool.side_effect} side effects and needs a person's approval",
            )
        result = await tool.handler(ctx, parsed)
        return ToolResult(content=sanitize(result.content), data=result.data)
    except ToolError as exc:
        status, error_code = "error", exc.code
        raise
    except Exception as exc:
        status, error_code = "error", "tool_failed"
        log.warning("tool_failed", tool=name, error=str(exc)[:300])
        raise ToolError("tool_failed", f"{name} failed") from exc
    finally:
        async with tenant_transaction(sessions, ctx.tenant_id) as session:
            await record_event(
                session,
                tenant_id=ctx.tenant_id,
                actor=Actor(type="user", id=ctx.user_id),
                action="tool.called",
                resource_type="tool",
                resource_id=name,
                details={
                    "caller": caller,
                    "side_effect": tool.side_effect if tool else None,
                    "status": status,
                    "error": error_code,
                    "argument_names": sorted(arguments),  # values may be sensitive
                    "ms": round((time.perf_counter() - started) * 1000),
                },
            )
