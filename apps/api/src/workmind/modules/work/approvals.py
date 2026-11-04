"""Human approval for actions with side effects (ARCHITECTURE 14.2, ADR 0009).

    agent or workflow wants to act -> propose(): the action is stored, nothing happens
    a person decides                -> decide(): approve (optionally edited) or reject
    approve                         -> the stored action runs once, as the person who asked,
                                       with the access they have *now*

Nothing waits in memory: a decision can come days later, from another device, after restarts.
"""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from workmind.core.context import RequestContext
from workmind.core.errors import ApiError
from workmind.core.tenancy import tenant_transaction
from workmind.modules.governance.audit import Actor, record_event
from workmind.modules.identity.service import load_identity
from workmind.modules.tools.execute import execute_tool
from workmind.modules.tools.registry import ToolError, ToolRegistry
from workmind.modules.work.models import Approval
from workmind.modules.work.notifications import notify

DEFAULT_TTL = timedelta(days=3)
# Called after an approval is executed, rejected or expired: resumes the workflow run it belongs to.
OnDecided = Callable[[Approval], Awaitable[None]]


async def propose(
    session: AsyncSession,
    ctx: RequestContext,
    *,
    tool: str,
    arguments: dict[str, Any],
    summary: str,
    approver_id: uuid.UUID | None = None,
    origin: dict[str, Any] | None = None,
    ttl: timedelta = DEFAULT_TTL,
) -> Approval:
    """Stores the action. By default the person who asked approves it themselves."""
    approval = Approval(
        tenant_id=ctx.tenant_id,
        tool=tool,
        arguments=arguments,
        summary=summary[:1000],
        requested_by=ctx.user_id,
        approver_id=approver_id or ctx.user_id,
        expires_at=datetime.now(UTC) + ttl,
        origin={**(origin or {}), "scopes": sorted(ctx.scopes)},
    )
    session.add(approval)
    await session.flush()
    await notify(
        session,
        ctx.tenant_id,
        approval.approver_id,
        kind="approval.requested",
        title=f"Approval needed: {summary[:120]}",
        link=f"/approvals/{approval.id}",
    )
    await record_event(
        session,
        tenant_id=ctx.tenant_id,
        actor=Actor(type="user", id=ctx.user_id),
        action="approval.requested",
        resource_type="approval",
        resource_id=str(approval.id),
        details={
            "tool": tool,
            "approver": str(approval.approver_id),
            "argument_names": sorted(arguments),
        },
    )
    return approval


async def _requester_context(session: AsyncSession, approval: Approval) -> RequestContext | None:
    """The requester as they are now: disabled people's actions don't run, and scopes granted
    when the action was proposed (e.g. an access token's) still bound it."""
    identity = await load_identity(session, approval.requested_by)
    if identity is None or identity.user.status != "active":
        return None
    return RequestContext(
        request_id=f"approval-{approval.id}",
        tenant_id=approval.tenant_id,
        user_id=approval.requested_by,
        org_role=identity.user.org_role,
        principals=identity.principals,
        auth_method="service",
        scopes=frozenset(approval.origin.get("scopes") or ["*"]),
    )


async def decide(
    sessions: async_sessionmaker[AsyncSession],
    registry: ToolRegistry,
    decider: RequestContext,
    approval_id: uuid.UUID,
    *,
    decision: Literal["approve", "reject"],
    edited_arguments: dict[str, Any] | None = None,
    note: str | None = None,
    on_decided: OnDecided | None = None,
) -> Approval:
    now = datetime.now(UTC)
    async with tenant_transaction(sessions, decider.tenant_id) as session:
        approval = await session.get(Approval, approval_id)
        if approval is None or approval.approver_id != decider.user_id:
            raise ApiError(404, "not_found", "Approval not found")
        # Claim it atomically: a double click or two devices can never run the action twice.
        claimed = await session.scalar(
            update(Approval)
            .where(
                Approval.id == approval_id, Approval.status == "pending", Approval.expires_at > now
            )
            .values(
                status="approved" if decision == "approve" else "rejected",
                decided_by=decider.user_id,
                decided_at=now,
                decision_note=(note or "").strip()[:2000] or None,
                edited_arguments=edited_arguments if decision == "approve" else None,
            )
            .returning(Approval.id)
        )
        if claimed is None:
            raise ApiError(409, "not_pending", "This approval was already decided or has expired")
        await session.refresh(approval)
        requester = await _requester_context(session, approval) if decision == "approve" else None
        await record_event(
            session,
            tenant_id=decider.tenant_id,
            actor=Actor(type="user", id=decider.user_id),
            action=f"approval.{decision}d",
            resource_type="approval",
            resource_id=str(approval.id),
            details={"tool": approval.tool, "edited": edited_arguments is not None},
        )
    if decision == "approve":
        await _execute(sessions, registry, approval, requester)
    await _close(sessions, approval)
    if on_decided is not None:
        await on_decided(approval)
    return approval


async def _execute(
    sessions: async_sessionmaker[AsyncSession],
    registry: ToolRegistry,
    approval: Approval,
    requester: RequestContext | None,
) -> None:
    arguments = approval.edited_arguments or approval.arguments
    if requester is None:
        status, result = "failed", {"error": "the requester is no longer active"}
    else:
        try:
            outcome = await execute_tool(
                registry,
                sessions,
                requester,
                approval.tool,
                arguments,
                approved=True,
                caller="approval",
            )
            status, result = "executed", {"content": outcome.content[:4000], "data": outcome.data}
        except ToolError as exc:
            status, result = "failed", {"error": f"{exc.code}: {exc}"}
    async with tenant_transaction(sessions, approval.tenant_id) as session:
        stored = await session.get(Approval, approval.id)
        assert stored is not None  # noqa: S101
        stored.status, stored.result = status, result
    approval.status, approval.result = status, result


async def _close(sessions: async_sessionmaker[AsyncSession], approval: Approval) -> None:
    """Tells the requester what happened (unless they decided it themselves)."""
    async with tenant_transaction(sessions, approval.tenant_id) as session:
        if approval.decided_by != approval.requested_by:
            await notify(
                session,
                approval.tenant_id,
                approval.requested_by,
                kind=f"approval.{approval.status}",
                title=f"{approval.status.capitalize()}: {approval.summary[:120]}",
                link=f"/approvals/{approval.id}",
            )


async def expire_due(
    sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> list[Approval]:
    """Pending approvals past their deadline expire; nothing runs. Called by the scheduler."""
    now = datetime.now(UTC)
    async with tenant_transaction(sessions, tenant_id) as session:
        expired = list(
            (
                await session.scalars(
                    update(Approval)
                    .where(Approval.status == "pending", Approval.expires_at <= now)
                    .values(status="expired")
                    .returning(Approval)
                )
            ).all()
        )
        for approval in expired:
            await notify(
                session,
                tenant_id,
                approval.requested_by,
                kind="approval.expired",
                title=f"Expired without a decision: {approval.summary[:120]}",
                link=f"/approvals/{approval.id}",
            )
    return expired


async def pending_for(session: AsyncSession, user_id: uuid.UUID) -> list[Approval]:
    return list(
        (
            await session.scalars(
                select(Approval)
                .where(Approval.approver_id == user_id, Approval.status == "pending")
                .order_by(Approval.created_at.desc())
            )
        ).all()
    )
