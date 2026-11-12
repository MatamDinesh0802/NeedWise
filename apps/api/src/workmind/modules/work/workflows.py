"""Declarative workflows (ARCHITECTURE section 14.3).

    key: weekly-ops-report
    trigger: {schedule: "0 8 * * MON"}          # or manual only, or {event: document.ready}
    steps:
      - id: gather
        research: Summarize this week's operations: incidents, shipping, safety.
      - id: send
        tool: send_email
        arguments: {to: [leadership@acme.test], subject: Weekly ops report,
                    body: "{{ steps.gather.text }}"}
        approver: priya.raman@acme.test         # side effects stop here until a person decides

A run acts as the workflow's owner, with the owner's current access. Research steps are
read-only. A step with side effects becomes an approval and the run pauses; the decision
resumes it (ADR 0009: no graph state in memory, nothing re-planned on resume).
"""

import re
import uuid
from datetime import UTC, date, datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from workmind.core.context import RequestContext
from workmind.core.tenancy import tenant_transaction
from workmind.modules.agents.research import research
from workmind.modules.governance.audit import Actor, record_event
from workmind.modules.identity.models import SLUG_PATTERN, Tenant, User
from workmind.modules.identity.service import load_identity
from workmind.modules.tools.execute import execute_tool
from workmind.modules.tools.registry import ToolError, ToolRegistry
from workmind.modules.work.approvals import propose
from workmind.modules.work.cron import Cron, CronError
from workmind.modules.work.links import workflow_link
from workmind.modules.work.models import Approval, Workflow, WorkflowRun
from workmind.modules.work.notifications import notify
from workmind.providers.llm import LLMProvider

PLACEHOLDER = re.compile(r"\{\{\s*([\w.]+)\s*\}\}")


class Trigger(BaseModel):
    schedule: str | None = None
    event: Literal["document.ready"] | None = None
    collection: str | None = None  # event filter: a collection key

    @model_validator(mode="after")
    def _valid(self) -> Self:
        if self.schedule:
            try:
                Cron(self.schedule)
            except CronError as exc:
                raise ValueError(f"schedule: {exc}") from exc
        return self


class Step(BaseModel):
    id: str = Field(pattern=SLUG_PATTERN)
    research: str | None = None  # a question for the research agent (read-only tools)
    tool: str | None = None  # or one tool call
    arguments: dict[str, Any] = {}
    approver: str | None = None  # email; default: the workflow's owner

    @model_validator(mode="after")
    def _one_kind(self) -> Self:
        if (self.research is None) == (self.tool is None):
            raise ValueError(f"step {self.id}: give either `research` or `tool`")
        return self


class Definition(BaseModel):
    description: str = ""
    trigger: Trigger = Trigger()
    steps: list[Step] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def _unique_ids(self) -> Self:
        ids = [s.id for s in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("step ids must be unique")
        return self


def render(value: Any, context: dict[str, Any]) -> Any:
    """Fills {{ steps.<id>.text }}, {{ steps.<id>.data.<key> }}, {{ today }}, {{ event.<key> }}.
    A string that is exactly one placeholder keeps the value's type (e.g. a list of recipients)."""
    if isinstance(value, str):
        whole = PLACEHOLDER.fullmatch(value.strip())
        if whole:
            return _lookup(whole.group(1), context)
        return PLACEHOLDER.sub(lambda m: str(_lookup(m.group(1), context)), value)
    if isinstance(value, list):
        return [render(v, context) for v in value]  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, dict):
        return {k: render(v, context) for k, v in value.items()}  # pyright: ignore[reportUnknownVariableType]
    return value


def _lookup(path: str, context: dict[str, Any]) -> Any:
    current: Any = context
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return ""
        current = current[part]  # pyright: ignore[reportUnknownVariableType]
    return current  # pyright: ignore[reportUnknownVariableType]


class WorkflowDeps:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        llm: LLMProvider,
        tools: ToolRegistry,
    ) -> None:
        self.sessions, self.llm, self.tools = sessions, llm, tools


async def _owner(session: AsyncSession, workflow: Workflow) -> RequestContext | None:
    identity = await load_identity(session, workflow.owner_id)
    if identity is None or identity.user.status != "active":
        return None
    return RequestContext(
        request_id=f"workflow-{workflow.key}",
        tenant_id=workflow.tenant_id,
        user_id=workflow.owner_id,
        org_role=identity.user.org_role,
        principals=identity.principals,
        auth_method="service",
        scopes=frozenset({"*"}),
    )


async def create_run(
    deps: WorkflowDeps,
    tenant_id: uuid.UUID,
    workflow_id: uuid.UUID,
    *,
    trigger: str,
    event: dict[str, Any] | None = None,
) -> uuid.UUID:
    async with tenant_transaction(deps.sessions, tenant_id) as session:
        run = WorkflowRun(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            trigger=trigger,
            steps={"_event": event or {}},
        )
        session.add(run)
        await session.flush()
        return run.id


async def start_run(
    deps: WorkflowDeps,
    tenant_id: uuid.UUID,
    workflow_id: uuid.UUID,
    *,
    trigger: str,
    event: dict[str, Any] | None = None,
) -> WorkflowRun:
    run_id = await create_run(deps, tenant_id, workflow_id, trigger=trigger, event=event)
    return await advance(deps, tenant_id, run_id)


async def advance(deps: WorkflowDeps, tenant_id: uuid.UUID, run_id: uuid.UUID) -> WorkflowRun:
    """Runs the next steps until the workflow finishes, fails, or waits for a person."""
    async with tenant_transaction(deps.sessions, tenant_id) as session:
        run = await session.get(WorkflowRun, run_id)
        assert run is not None  # noqa: S101
        workflow = await session.get(Workflow, run.workflow_id)
        assert workflow is not None  # noqa: S101
        owner = await _owner(session, workflow)
        tenant = await session.get(Tenant, tenant_id)
    if owner is None:
        return await _finish(deps, run, "failed", "the workflow's owner is no longer active")
    definition = Definition.model_validate(workflow.definition)
    outputs: dict[str, Any] = dict(run.steps)
    context = {
        "steps": outputs,
        "today": date.today().isoformat(),
        "event": outputs.get("_event", {}),
    }
    for step in definition.steps:
        if outputs.get(step.id, {}).get("status") == "done":
            continue
        try:
            if step.research is not None:
                question = str(render(step.research, context))
                result = await research(
                    deps.llm,
                    deps.tools,
                    deps.sessions,
                    owner,
                    question,
                    organization=tenant.name if tenant else "the company",
                    propose_actions=False,  # research steps only read
                )
                outputs[step.id] = {"status": "done", "text": result.text}
            else:
                assert step.tool is not None  # noqa: S101
                tool = deps.tools.get(step.tool)
                if tool is None:
                    raise ToolError("unknown_tool", f"No tool named {step.tool!r}")
                arguments = render(step.arguments, context)
                if tool.side_effect == "read":
                    done = await execute_tool(
                        deps.tools, deps.sessions, owner, step.tool, arguments, caller="workflow"
                    )
                    outputs[step.id] = {
                        "status": "done",
                        "text": done.content,
                        "data": done.data or {},
                    }
                else:
                    try:  # an action that cannot run must fail now, not after someone approves it
                        tool.validate(arguments)
                    except ValueError as exc:
                        raise ToolError("invalid_input", str(exc)[:300]) from exc
                    await _pause(
                        deps,
                        run,
                        workflow,
                        owner,
                        step,
                        tool.describe(arguments),
                        arguments,
                        outputs,
                    )
                    return run
        except ToolError as exc:
            outputs[step.id] = {"status": "failed", "error": f"{exc.code}: {exc}"}
            await _save(deps, run, outputs, step.id)
            return await _finish(deps, run, "failed", f"step {step.id}: {exc}")
        await _save(deps, run, outputs, step.id)
    return await _finish(deps, run, "succeeded", None)


async def _pause(
    deps: WorkflowDeps,
    run: WorkflowRun,
    workflow: Workflow,
    owner: RequestContext,
    step: Step,
    summary: str,
    arguments: dict[str, Any],
    outputs: dict[str, Any],
) -> None:
    async with tenant_transaction(deps.sessions, run.tenant_id) as session:
        approver_id = owner.user_id
        if step.approver:
            approver_id = (
                await session.scalar(select(User.id).where(User.email == step.approver.lower()))
                or owner.user_id
            )
        approval = await propose(
            session,
            owner,
            tool=step.tool or "",
            arguments=arguments,
            summary=f"{workflow.name}: {summary}",
            approver_id=approver_id,
            origin={
                "via": "workflow",
                "workflow": workflow.key,
                "workflow_run_id": str(run.id),
                "step": step.id,
            },
        )
        outputs[step.id] = {"status": "waiting_approval", "approval_id": str(approval.id)}
        stored = await session.get(WorkflowRun, run.id)
        assert stored is not None  # noqa: S101
        stored.steps, stored.current_step, stored.status = outputs, step.id, "waiting_approval"
    run.status = "waiting_approval"


async def resume_after(deps: WorkflowDeps, approval: Approval) -> None:
    """The approval's decision is in: continue the run it belongs to (or end it)."""
    run_id = approval.origin.get("workflow_run_id")
    step = approval.origin.get("step")
    if not run_id or not step:
        return
    async with tenant_transaction(deps.sessions, approval.tenant_id) as session:
        run = await session.get(WorkflowRun, uuid.UUID(run_id))
        if run is None or run.status != "waiting_approval":
            return
        outputs = dict(run.steps)
        if approval.status == "executed":
            result = approval.result or {}
            outputs[step] = {
                "status": "done",
                "text": result.get("content", ""),
                "data": result.get("data") or {},
            }
        else:
            outputs[step] = {
                "status": approval.status,
                "error": (approval.result or {}).get("error"),
            }
        run.steps, run.status = outputs, "running"
    if approval.status == "executed":
        await advance(deps, approval.tenant_id, uuid.UUID(run_id))
    else:
        stored = await _get(deps, approval.tenant_id, uuid.UUID(run_id))
        await _finish(
            deps,
            stored,
            "cancelled" if approval.status in ("rejected", "expired") else "failed",
            f"step {step}: {approval.status}",
        )


async def _get(deps: WorkflowDeps, tenant_id: uuid.UUID, run_id: uuid.UUID) -> WorkflowRun:
    async with tenant_transaction(deps.sessions, tenant_id) as session:
        run = await session.get(WorkflowRun, run_id)
        assert run is not None  # noqa: S101
        return run


async def _save(deps: WorkflowDeps, run: WorkflowRun, outputs: dict[str, Any], step: str) -> None:
    async with tenant_transaction(deps.sessions, run.tenant_id) as session:
        stored = await session.get(WorkflowRun, run.id)
        assert stored is not None  # noqa: S101
        stored.steps, stored.current_step = dict(outputs), step


async def _finish(
    deps: WorkflowDeps, run: WorkflowRun, status: str, error: str | None
) -> WorkflowRun:
    async with tenant_transaction(deps.sessions, run.tenant_id) as session:
        stored = await session.get(WorkflowRun, run.id)
        assert stored is not None  # noqa: S101
        stored.status, stored.error, stored.finished_at = status, error, datetime.now(UTC)
        workflow = await session.get(Workflow, stored.workflow_id)
        if workflow is not None:
            if status != "succeeded":
                await notify(
                    session,
                    run.tenant_id,
                    workflow.owner_id,
                    kind=f"workflow.{status}",
                    title=f"{workflow.name}: {status}",
                    body=error or "",
                    link=workflow_link(workflow.key, run.id),
                )
            await record_event(
                session,
                tenant_id=run.tenant_id,
                actor=Actor.system(),
                action=f"workflow.{status}",
                resource_type="workflow",
                resource_id=workflow.key,
                details={"run_id": str(run.id), "error": error},
            )
        await session.flush()
        return stored


async def due_workflows(
    sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID, now: datetime
) -> list[uuid.UUID]:
    """Scheduled workflows whose cron matches this minute and haven't run in it yet."""
    minute = now.replace(second=0, microsecond=0)
    due: list[uuid.UUID] = []
    async with tenant_transaction(sessions, tenant_id) as session:
        for workflow in await session.scalars(select(Workflow).where(Workflow.enabled.is_(True))):
            trigger: dict[str, Any] = workflow.definition.get("trigger") or {}
            schedule = trigger.get("schedule")
            if not isinstance(schedule, str) or not Cron(schedule).matches(minute):
                continue
            if workflow.last_scheduled_at is not None and workflow.last_scheduled_at >= minute:
                continue
            workflow.last_scheduled_at = minute
            due.append(workflow.id)
    return due
