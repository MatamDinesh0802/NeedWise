"""Approvals inbox, tasks, notifications, and workflows (ARCHITECTURE section 14)."""

import uuid
from datetime import UTC, date, datetime, time
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from workmind.api.deps import require_scope, resources_of
from workmind.api.v1.admin import AdminContext
from workmind.core.context import RequestContext
from workmind.core.errors import ApiError
from workmind.core.tenancy import tenant_transaction
from workmind.modules.governance.audit import Actor, record_event
from workmind.modules.identity.models import SLUG_PATTERN, Department, DepartmentMember, User
from workmind.modules.work.approvals import decide
from workmind.modules.work.models import Approval, Notification, Task, Workflow, WorkflowRun
from workmind.modules.work.notifications import notify
from workmind.modules.work.workflows import (
    Definition,
    WorkflowDeps,
    advance,
    create_run,
    resume_after,
)

router = APIRouter(tags=["work"])
admin_router = APIRouter(prefix="/admin/workflows", tags=["admin"])
AskContext = Annotated[RequestContext, require_scope("assistant:ask")]


# --- Approvals --------------------------------------------------------------------------------


class ApprovalOut(BaseModel):
    id: uuid.UUID
    tool: str
    summary: str
    arguments: dict[str, Any]
    edited_arguments: dict[str, Any] | None
    status: str
    requested_by: uuid.UUID
    approver_id: uuid.UUID
    decided_at: datetime | None
    decision_note: str | None
    expires_at: datetime
    result: dict[str, Any] | None
    origin: dict[str, Any]
    created_at: datetime


class Decision(BaseModel):
    decision: Literal["approve", "reject"]
    arguments: dict[str, Any] | None = Field(default=None, description="edited arguments")
    note: str | None = Field(default=None, max_length=2000)


def _approval(a: Approval) -> ApprovalOut:
    return ApprovalOut(
        id=a.id,
        tool=a.tool,
        summary=a.summary,
        arguments=a.arguments,
        edited_arguments=a.edited_arguments,
        status=a.status,
        requested_by=a.requested_by,
        approver_id=a.approver_id,
        decided_at=a.decided_at,
        decision_note=a.decision_note,
        expires_at=a.expires_at,
        result=a.result,
        origin={k: v for k, v in a.origin.items() if k != "scopes"},
        created_at=a.created_at,
    )


@router.get("/approvals")
async def list_approvals(
    ctx: AskContext, request: Request, status: str = "pending"
) -> list[ApprovalOut]:
    """Waiting for me (as approver), plus what I asked for (as requester)."""
    query = select(Approval).where(
        or_(Approval.approver_id == ctx.user_id, Approval.requested_by == ctx.user_id)
    )
    if status != "all":
        query = query.where(Approval.status == status)
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        rows = (await session.scalars(query.order_by(Approval.created_at.desc()).limit(100))).all()
    return [_approval(a) for a in rows]


@router.get("/approvals/{approval_id}")
async def get_approval(ctx: AskContext, request: Request, approval_id: uuid.UUID) -> ApprovalOut:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        approval = await session.get(Approval, approval_id)
    if approval is None or ctx.user_id not in (approval.approver_id, approval.requested_by):
        raise ApiError(404, "not_found", "Approval not found")
    return _approval(approval)


@router.post("/approvals/{approval_id}/decision")
async def decide_approval(
    ctx: AskContext, request: Request, approval_id: uuid.UUID, body: Decision
) -> ApprovalOut:
    resources = resources_of(request)
    if body.arguments is not None:  # an edit must still fit the tool
        async with tenant_transaction(resources.db, ctx.tenant_id) as session:
            approval = await session.get(Approval, approval_id)
        tool = resources.tools.get(approval.tool) if approval else None
        if tool is not None:
            try:
                tool.validate(body.arguments)
            except (ValueError, ValidationError) as exc:
                raise ApiError(
                    422, "invalid_input", "The edited action is not valid", str(exc)[:500]
                ) from exc
    deps = WorkflowDeps(resources.db, resources.llm, resources.tools)
    decided = await decide(
        resources.db,
        resources.tools,
        ctx,
        approval_id,
        decision=body.decision,
        edited_arguments=body.arguments,
        note=body.note,
        on_decided=lambda a: _resume_in_background(request, deps, a),
    )
    return _approval(decided)


async def _resume_in_background(request: Request, deps: WorkflowDeps, approval: Approval) -> None:
    """A workflow waiting on this approval continues without holding the request."""
    if approval.origin.get("workflow_run_id"):
        resources_of(request).runs.start(resume_after(deps, approval))


# --- Tasks ------------------------------------------------------------------------------------


class TaskOut(BaseModel):
    id: uuid.UUID
    title: str
    description: str
    status: str
    assignee_id: uuid.UUID | None
    department_id: uuid.UUID | None
    created_by: uuid.UUID | None
    due_at: datetime | None
    origin: dict[str, Any]
    created_at: datetime


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    assignee_id: uuid.UUID | None = None
    department: str | None = Field(default=None, pattern=SLUG_PATTERN)
    due: date | None = None


class TaskUpdate(BaseModel):
    status: Literal["open", "in_progress", "done", "cancelled"] | None = None
    assignee_id: uuid.UUID | None = None


def _task(t: Task) -> TaskOut:
    return TaskOut(
        id=t.id,
        title=t.title,
        description=t.description,
        status=t.status,
        assignee_id=t.assignee_id,
        department_id=t.department_id,
        created_by=t.created_by,
        due_at=t.due_at,
        origin=t.origin,
        created_at=t.created_at,
    )


async def _my_departments(session: AsyncSession, user_id: uuid.UUID) -> list[uuid.UUID]:
    return list(
        (
            await session.scalars(
                select(DepartmentMember.department_id).where(DepartmentMember.user_id == user_id)
            )
        ).all()
    )


@router.get("/tasks")
async def list_tasks(
    ctx: AskContext, request: Request, scope: Literal["mine", "queues", "created"] = "mine"
) -> list[TaskOut]:
    """Mine (assigned to me), my departments' queues (unassigned), or the ones I created."""
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        query = select(Task).where(Task.status.in_(("open", "in_progress")))
        if scope == "mine":
            query = query.where(Task.assignee_id == ctx.user_id)
        elif scope == "queues":
            query = query.where(
                Task.assignee_id.is_(None),
                Task.department_id.in_(await _my_departments(session, ctx.user_id)),
            )
        else:
            query = select(Task).where(Task.created_by == ctx.user_id)
        rows = (await session.scalars(query.order_by(Task.created_at.desc()).limit(200))).all()
    return [_task(t) for t in rows]


@router.post("/tasks", status_code=201)
async def create_task(ctx: AskContext, request: Request, body: TaskCreate) -> TaskOut:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        department = (
            await session.scalar(select(Department).where(Department.slug == body.department))
            if body.department
            else None
        )
        if body.department and department is None:
            raise ApiError(422, "unknown_department", "Unknown department")
        if body.assignee_id and await session.get(User, body.assignee_id) is None:
            raise ApiError(422, "unknown_user", "Unknown assignee")
        task = Task(
            tenant_id=ctx.tenant_id,
            title=body.title,
            description=body.description,
            assignee_id=body.assignee_id,
            department_id=department.id if department else None,
            created_by=ctx.user_id,
            due_at=datetime.combine(body.due, time(17, 0), UTC) if body.due else None,
            origin={"via": "web"},
        )
        session.add(task)
        await session.flush()
        if body.assignee_id and body.assignee_id != ctx.user_id:
            await notify(
                session,
                ctx.tenant_id,
                body.assignee_id,
                kind="task.assigned",
                title=f"New task: {body.title}",
                link=f"/tasks/{task.id}",
            )
        return _task(task)


@router.patch("/tasks/{task_id}")
async def update_task(
    ctx: AskContext, request: Request, task_id: uuid.UUID, body: TaskUpdate
) -> TaskOut:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        task = await session.get(Task, task_id)
        mine = task is not None and (
            ctx.user_id in (task.assignee_id, task.created_by)
            or (task.department_id in await _my_departments(session, ctx.user_id))
        )
        if task is None or not mine:
            raise ApiError(404, "not_found", "Task not found")
        if body.status:
            task.status = body.status
        if body.assignee_id:
            task.assignee_id = body.assignee_id  # claiming from a queue, or handing over
        await record_event(
            session,
            tenant_id=ctx.tenant_id,
            actor=Actor(type="user", id=ctx.user_id),
            action="task.updated",
            resource_type="task",
            resource_id=str(task.id),
            details=body.model_dump(exclude_none=True, mode="json"),
        )
        return _task(task)


# --- Notifications ----------------------------------------------------------------------------


class NotificationOut(BaseModel):
    id: uuid.UUID
    kind: str
    title: str
    body: str
    link: str | None
    read_at: datetime | None
    created_at: datetime


class MarkRead(BaseModel):
    ids: list[uuid.UUID] | None = None  # None: all


@router.get("/notifications")
async def list_notifications(ctx: AskContext, request: Request) -> list[NotificationOut]:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        rows = (
            await session.scalars(
                select(Notification)
                .where(Notification.user_id == ctx.user_id)
                .order_by(Notification.read_at.is_not(None), Notification.created_at.desc())
                .limit(50)
            )
        ).all()
    return [
        NotificationOut(
            id=n.id,
            kind=n.kind,
            title=n.title,
            body=n.body,
            link=n.link,
            read_at=n.read_at,
            created_at=n.created_at,
        )
        for n in rows
    ]


@router.post("/notifications/read", status_code=204)
async def mark_read(ctx: AskContext, request: Request, body: MarkRead) -> Response:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        query = update(Notification).where(
            Notification.user_id == ctx.user_id, Notification.read_at.is_(None)
        )
        if body.ids is not None:
            query = query.where(Notification.id.in_(body.ids))
        await session.execute(query.values(read_at=datetime.now(UTC)))
    return Response(status_code=204)


# --- Workflows (admin) ------------------------------------------------------------------------


class WorkflowIn(BaseModel):
    key: str = Field(pattern=SLUG_PATTERN, max_length=60)
    name: str = Field(min_length=1, max_length=120)
    definition: dict[str, Any]
    enabled: bool = True


class WorkflowOut(BaseModel):
    id: uuid.UUID
    key: str
    name: str
    definition: dict[str, Any]
    enabled: bool
    owner_id: uuid.UUID


class RunOut(BaseModel):
    id: uuid.UUID
    status: str
    trigger: str
    steps: dict[str, Any]
    current_step: str | None
    error: str | None
    created_at: datetime
    finished_at: datetime | None


def _workflow(w: Workflow) -> WorkflowOut:
    return WorkflowOut(
        id=w.id,
        key=w.key,
        name=w.name,
        definition=w.definition,
        enabled=w.enabled,
        owner_id=w.owner_id,
    )


def _run(r: WorkflowRun) -> RunOut:
    steps = {k: v for k, v in r.steps.items() if k != "_event"}
    return RunOut(
        id=r.id,
        status=r.status,
        trigger=r.trigger,
        steps=steps,
        current_step=r.current_step,
        error=r.error,
        created_at=r.created_at,
        finished_at=r.finished_at,
    )


def _definition(raw: dict[str, Any]) -> dict[str, Any]:
    try:
        return Definition.model_validate(raw).model_dump(mode="json", exclude_none=True)
    except ValidationError as exc:
        raise ApiError(
            422, "invalid_workflow", "The workflow definition is invalid", str(exc)[:800]
        ) from exc


@admin_router.get("")
async def list_workflows(ctx: AdminContext, request: Request) -> list[WorkflowOut]:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        return [
            _workflow(w) for w in await session.scalars(select(Workflow).order_by(Workflow.name))
        ]


@admin_router.put("/{key}")
async def save_workflow(
    ctx: AdminContext, request: Request, key: str, body: WorkflowIn
) -> WorkflowOut:
    """Creates or replaces a workflow. It runs as the admin who saved it."""
    if body.key != key:
        raise ApiError(422, "key_mismatch", "The key in the body must match the URL")
    definition = _definition(body.definition)
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        workflow = await session.scalar(select(Workflow).where(Workflow.key == key))
        if workflow is None:
            workflow = Workflow(
                tenant_id=ctx.tenant_id,
                key=key,
                owner_id=ctx.user_id,
                name=body.name,
                definition=definition,
            )
            session.add(workflow)
        workflow.name, workflow.definition, workflow.enabled, workflow.owner_id = (
            body.name,
            definition,
            body.enabled,
            ctx.user_id,
        )
        await session.flush()
        await record_event(
            session,
            tenant_id=ctx.tenant_id,
            actor=Actor(type="user", id=ctx.user_id),
            action="admin.workflow.saved",
            resource_type="workflow",
            resource_id=key,
            details={"steps": [s["id"] for s in definition["steps"]], "enabled": body.enabled},
        )
        return _workflow(workflow)


@admin_router.post("/{key}/run", status_code=202)
async def run_workflow(ctx: AdminContext, request: Request, key: str) -> RunOut:
    resources = resources_of(request)
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        workflow = await session.scalar(select(Workflow).where(Workflow.key == key))
        if workflow is None:
            raise ApiError(404, "not_found", "Workflow not found")
        workflow_id = workflow.id
    deps = WorkflowDeps(resources.db, resources.llm, resources.tools)
    run_id = await create_run(deps, ctx.tenant_id, workflow_id, trigger="manual")
    resources.runs.start(_advance(deps, ctx.tenant_id, run_id))  # may take a minute: not here
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        run = await session.get(WorkflowRun, run_id)
    assert run is not None  # noqa: S101
    return _run(run)


async def _advance(deps: WorkflowDeps, tenant_id: uuid.UUID, run_id: uuid.UUID) -> None:
    await advance(deps, tenant_id, run_id)


@admin_router.get("/{key}/runs")
async def list_runs(ctx: AdminContext, request: Request, key: str) -> list[RunOut]:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        workflow = await session.scalar(select(Workflow).where(Workflow.key == key))
        if workflow is None:
            raise ApiError(404, "not_found", "Workflow not found")
        rows = (
            await session.scalars(
                select(WorkflowRun)
                .where(WorkflowRun.workflow_id == workflow.id)
                .order_by(WorkflowRun.created_at.desc())
                .limit(50)
            )
        ).all()
    return [_run(r) for r in rows]
