"""Human approval for actions with side effects (ARCHITECTURE 14.2, ADR 0009).

    agent or workflow wants to act -> propose(): the action is stored, nothing happens
    a person decides                -> decide(): approve (optionally edited) or reject
    approve                         -> the stored action runs once, as the person who asked,
                                       with the access they have *now*

Nothing waits in memory: a decision can come days later, from another device, after restarts.

Most approvals hold a tool call. An approval *kind* (ApprovalKind) is a decision the core itself
carries out instead, such as making a document official (modules/knowledge/governance.py). Kinds
are never tools: no agent or workflow can propose one, and their arguments cannot be edited.
One request may go to several approvers (the same `origin.request_id`); the first decision
wins and the others become "superseded".

Approver rules (approvers.py) say who those approvers are: a named person, the org admins, a
department's managers, any one of a group, or a chain of stages that all approve in order. A
chain's stage that approves is "forwarded": the next stage's approvers get their copies, with
the arguments as approved so far, and only the last stage's approval runs the action. Rules
never resolve to the person who asked, and a chain never to someone who approved one of its
earlier stages (earlier_deciders): two stages are two people. An escalation (escalation.py)
adds copies for the escalation target to the same stage, under the same two exclusions.
"""

import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from needwise.core.context import RequestContext
from needwise.core.errors import ApiError
from needwise.core.tenancy import tenant_transaction
from needwise.modules.governance.audit import Actor, record_event
from needwise.modules.identity.models import User
from needwise.modules.identity.service import load_identity
from needwise.modules.tools.execute import execute_tool
from needwise.modules.tools.registry import ToolError, ToolRegistry
from needwise.modules.work.approvers import (
    MAX_STAGES,
    Rule,
    RuleError,
    describe,
    first_stage,
    resolve_stage,
    rule_json,
    stored_rule,
)
from needwise.modules.work.events import approval_decided
from needwise.modules.work.links import approval_link
from needwise.modules.work.models import Approval
from needwise.modules.work.notifications import notify

DEFAULT_TTL = timedelta(days=3)
# Called after an approval is executed, rejected or expired: resumes the workflow run it belongs to.
OnDecided = Callable[[Approval], Awaitable[None]]
# Starts workflow runs an event queued (approval.decided), e.g. Resources.start_workflow_runs.
StartRuns = Callable[[uuid.UUID, Sequence[uuid.UUID]], Awaitable[None]]


@dataclass(frozen=True)
class ApprovalKind:
    """What approving an approval of this kind does. `run` gets the approval and the person who
    approved it, runs in its own transaction, and returns the final status ("executed" or
    "failed") and a result for the record.

    `check`, when set, says why the person may not decide it (or None), before any decision is
    recorded: a refused decision changes nothing, so the other approvers' copies stay valid."""

    run: Callable[[Approval, RequestContext], Awaitable[tuple[str, dict[str, Any]]]]
    check: Callable[[AsyncSession, Approval, RequestContext], Awaitable[str | None]] | None = None


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
    rule: Rule | None = None,
) -> Approval:
    """Stores the action. By default the person who asked approves it themselves; with a
    `rule`, everyone its first stage resolves to gets a copy (raises RuleError when a stage
    has nobody). Returns the first copy."""
    stored_origin = {**(origin or {}), "scopes": sorted(ctx.scopes)}
    if rule is None:
        approvers = [approver_id or ctx.user_id]
    else:
        approvers = await first_stage(session, rule, ctx.user_id)
        stored_origin.setdefault("request_id", str(uuid.uuid4()))
    copies = [
        await add_copy(
            session,
            ctx.tenant_id,
            tool=tool,
            arguments=arguments,
            summary=summary,
            requested_by=ctx.user_id,
            approver_id=approver,
            expires_at=datetime.now(UTC) + ttl,
            origin=stored_origin,
            rule=rule,
            stage=0,
        )
        for approver in approvers
    ]
    details: dict[str, Any] = {
        "tool": tool,
        "approver": str(copies[0].approver_id),
        "argument_names": sorted(arguments),
    }
    if rule is not None:
        details |= {
            "approvers": [str(a) for a in approvers],
            "rule": describe(rule),
            "stages": len(rule),
        }
    await record_event(
        session,
        tenant_id=ctx.tenant_id,
        actor=Actor(type="user", id=ctx.user_id),
        action="approval.requested",
        resource_type="approval",
        resource_id=str(copies[0].id),
        details=details,
    )
    return copies[0]


async def add_copy(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    tool: str,
    arguments: dict[str, Any],
    summary: str,
    requested_by: uuid.UUID,
    approver_id: uuid.UUID,
    expires_at: datetime,
    origin: dict[str, Any],
    rule: Rule | None,
    stage: int,
    escalated_from: uuid.UUID | None = None,
    kind: str = "approval.requested",
    title: str | None = None,
) -> Approval:
    """One approver's copy of a request, and their notification."""
    approval = Approval(
        tenant_id=tenant_id,
        tool=tool,
        arguments=arguments,
        summary=summary[:1000],
        requested_by=requested_by,
        approver_id=approver_id,
        expires_at=expires_at,
        origin=dict(origin),
        approver_rule=rule_json(rule) if rule is not None else None,
        stage=stage,
        escalated_from=escalated_from,
    )
    session.add(approval)
    await session.flush()
    await notify(
        session,
        tenant_id,
        approver_id,
        kind=kind,
        title=title or f"Approval needed: {summary[:120]}",
        body=_stage_note(rule, stage),
        link=approval_link(approval.id),
    )
    return approval


async def earlier_deciders(session: AsyncSession, approval: Approval) -> list[uuid.UUID]:
    """Who approved the earlier stages of this approval's chain request, following
    `origin.forwarded_from` back to the first stage. Empty outside a chain's later stages."""
    deciders: list[uuid.UUID] = []
    seen: set[str] = set()
    current = approval.origin.get("forwarded_from")
    while isinstance(current, str) and current not in seen and len(seen) <= MAX_STAGES:
        seen.add(current)
        try:
            previous = await session.get(Approval, uuid.UUID(current))
        except ValueError:
            break
        if previous is None:
            break
        if previous.decided_by is not None:
            deciders.append(previous.decided_by)
        current = previous.origin.get("forwarded_from")
    return deciders


async def approver_names(session: AsyncSession, approval: Approval) -> list[str]:
    """The display names of everyone holding a copy of this approval's request, sorted."""
    request_id = approval.origin.get("request_id")
    holders = (
        select(Approval.approver_id).where(Approval.origin["request_id"].astext == str(request_id))
        if request_id
        else select(Approval.approver_id).where(Approval.id == approval.id)
    )
    return sorted(
        (await session.scalars(select(User.display_name).where(User.id.in_(holders)))).all()
    )


def _stage_note(rule: Rule | None, stage: int) -> str:
    if rule is None or len(rule) < 2:
        return ""
    return f"Step {stage + 1} of {len(rule)} ({describe(rule)})."


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
    kinds: Mapping[str, ApprovalKind] | None = None,
    start_runs: StartRuns | None = None,
) -> Approval:
    """`start_runs`: starts the workflow runs an `approval.decided` event queued (without it,
    the beat task starts them within a minute)."""
    now = datetime.now(UTC)
    async with tenant_transaction(sessions, decider.tenant_id) as session:
        approval = await session.get(Approval, approval_id)
        if approval is None or approval.approver_id != decider.user_id:
            raise ApiError(404, "not_found", "Approval not found")
        kind = (kinds or {}).get(approval.tool)
        if kind is not None and kind.check is not None and approval.status == "pending":
            problem = await kind.check(session, approval, decider)
            if problem is not None:
                raise ApiError(403, "not_allowed", "You may not decide this", problem)
        if decision == "approve" and decider.user_id in await earlier_deciders(session, approval):
            # Never issued (stages leave earlier deciders out); refused all the same.
            raise ApiError(
                403,
                "not_allowed",
                "You may not decide this",
                "You approved an earlier step of this request: another person approves this one.",
            )
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
        if decision == "approve" and edited_arguments is not None:
            _summarize_edit(registry, approval, edited_arguments)
        if request_id := approval.origin.get("request_id"):
            # The same request went to several approvers: this decision answers it for all.
            await session.execute(
                update(Approval)
                .where(
                    Approval.origin["request_id"].astext == str(request_id),
                    Approval.id != approval.id,
                    Approval.status == "pending",
                )
                .values(
                    status="superseded",
                    decided_by=decider.user_id,
                    decided_at=now,
                    # Where the request was decided: the other copies' pages link to it.
                    result={"decided_in": str(approval.id)},
                )
            )
        requester = await _requester_context(session, approval) if decision == "approve" else None
        rule = stored_rule(approval.approver_rule)
        await record_event(
            session,
            tenant_id=decider.tenant_id,
            actor=Actor(type="user", id=decider.user_id),
            action="approval.approved" if decision == "approve" else "approval.rejected",
            resource_type="approval",
            resource_id=str(approval.id),
            details={
                "tool": approval.tool,
                "edited": edited_arguments is not None,
                **({"stage": approval.stage + 1, "stages": len(rule)} if rule else {}),
            },
        )
        forwarded = (
            decision == "approve"
            and kind is None
            and rule is not None
            and approval.stage + 1 < len(rule)
            and await _forward(session, approval, rule, now)
        )
    if forwarded:
        return approval  # the next stage decides; nothing runs yet
    if decision == "approve" and kind is not None:
        await _run_kind(sessions, kind, approval, decider)
    elif decision == "approve" and approval.status == "approved":
        await _execute(sessions, registry, approval, requester)
    run_ids = await _close(sessions, approval)
    if on_decided is not None:
        await on_decided(approval)
    if run_ids and start_runs is not None:
        await start_runs(approval.tenant_id, run_ids)
    return approval


def _summarize_edit(registry: ToolRegistry, approval: Approval, edited: dict[str, Any]) -> None:
    """The approval's title describes what runs: an edit that changes the tool's own
    description of the call (e.g. an email's subject) changes it in the summary too, so the
    inbox and the card's header show the edited action, not the proposal."""
    tool = registry.get(approval.tool)
    if tool is None:
        return
    try:
        before, after = tool.describe(approval.arguments), tool.describe(edited)
    except (KeyError, TypeError, ValueError):
        return
    if before != after and before in approval.summary:
        approval.summary = approval.summary.replace(before, after, 1)[:1000]


async def _forward(session: AsyncSession, approval: Approval, rule: Rule, now: datetime) -> bool:
    """A chain's stage approved: the next stage's approvers get copies, with the arguments as
    approved so far. Returns False (and marks the request failed) when the next stage has
    nobody left who may decide: the action never runs half-approved."""
    stage = approval.stage + 1
    # Two stages are two people: whoever approved this stage or an earlier one gets no copy.
    decided = {approval.decided_by, *await earlier_deciders(session, approval)}
    try:
        resolved = await resolve_stage(session, rule[stage], approval.requested_by)
    except RuleError as exc:
        approvers, problem = [], str(exc)
    else:
        approvers = [p for p in resolved if p not in decided]
        problem = (
            f"nobody can approve step {stage + 1} of {len(rule)} ({describe([rule[stage]])})"
            + (": only people who approved an earlier step" if resolved else "")
        )
    if not approvers:
        approval.status, approval.result = "failed", {"error": problem}
        return False
    # The request's own time to decide each stage: an escalated copy was created later than
    # the original, with the original's deadline, so its own dates would shorten it.
    original = (
        await session.get(Approval, approval.escalated_from) if approval.escalated_from else None
    )
    timed = original or approval
    ttl = timed.expires_at - timed.created_at
    origin = {
        **approval.origin,
        "request_id": str(uuid.uuid4()),
        "forwarded_from": str(approval.id),
    }
    arguments = approval.edited_arguments or approval.arguments
    copies = [
        await add_copy(
            session,
            approval.tenant_id,
            tool=approval.tool,
            arguments=arguments,
            summary=approval.summary,
            requested_by=approval.requested_by,
            approver_id=approver,
            expires_at=now + ttl,
            origin=origin,
            rule=rule,
            stage=stage,
        )
        for approver in approvers
    ]
    approval.status = "forwarded"
    approval.result = {
        "stage": stage + 1,
        "stages": len(rule),
        "next": [str(c.id) for c in copies],
    }
    if approval.decided_by != approval.requested_by:
        await notify(
            session,
            approval.tenant_id,
            approval.requested_by,
            kind="approval.forwarded",
            title=f"Approved at step {stage} of {len(rule)}: {approval.summary[:120]}",
            body=approval.decision_note or "",
            link=approval_link(approval.id),
        )
    await record_event(
        session,
        tenant_id=approval.tenant_id,
        actor=Actor.system(),
        action="approval.forwarded",
        resource_type="approval",
        resource_id=str(approval.id),
        details={
            "tool": approval.tool,
            "stage": stage + 1,
            "stages": len(rule),
            "approvers": [str(a) for a in approvers],
            "next": [str(c.id) for c in copies],
        },
    )
    return True


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


async def _run_kind(
    sessions: async_sessionmaker[AsyncSession],
    kind: ApprovalKind,
    approval: Approval,
    decider: RequestContext,
) -> None:
    try:
        status, result = await kind.run(approval, decider)
    except ApiError as exc:
        status, result = "failed", {"error": f"{exc.code}: {exc.title}"}
    async with tenant_transaction(sessions, approval.tenant_id) as session:
        stored = await session.get(Approval, approval.id)
        assert stored is not None  # noqa: S101
        stored.status, stored.result = status, result
    approval.status, approval.result = status, result


async def _close(sessions: async_sessionmaker[AsyncSession], approval: Approval) -> list[uuid.UUID]:
    """Tells the requester what happened (unless they decided it themselves), with the
    approver's note, and queues the workflows that listen for `approval.decided`."""
    async with tenant_transaction(sessions, approval.tenant_id) as session:
        stored = await session.get(Approval, approval.id)
        if stored is not None and stored.status != approval.status:
            # A chain whose next stage had nobody: the request failed instead of moving on.
            stored.status, stored.result = approval.status, approval.result
        run_ids = await approval_decided(session, approval.tenant_id, approval)
        if approval.decided_by != approval.requested_by:
            await notify(
                session,
                approval.tenant_id,
                approval.requested_by,
                kind=f"approval.{approval.status}",
                title=f"{approval.status.capitalize()}: {approval.summary[:120]}",
                body=approval.decision_note or "",
                link=approval_link(approval.id),
            )
    return run_ids


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
        told: set[str] = set()
        for approval in expired:
            # A request sent to several approvers expires once for the person who asked.
            request_id = str(approval.origin.get("request_id") or approval.id)
            if request_id in told:
                continue
            told.add(request_id)
            await notify(
                session,
                tenant_id,
                approval.requested_by,
                kind="approval.expired",
                title=f"Expired without a decision: {approval.summary[:120]}",
                link=approval_link(approval.id),
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
