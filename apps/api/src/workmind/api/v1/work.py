"""Approvals inbox, tasks, notifications, and workflows (ARCHITECTURE section 14)."""

import uuid
from datetime import UTC, date, datetime, time
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from workmind.api.deps import rate_limit, require_scope, resources_of
from workmind.api.v1.admin import AdminContext
from workmind.core.context import RequestContext
from workmind.core.errors import ApiError
from workmind.core.resources import Resources
from workmind.core.tenancy import tenant_transaction
from workmind.modules.governance.audit import Actor, record_event
from workmind.modules.identity.models import SLUG_PATTERN, Department, DepartmentMember, User
from workmind.modules.knowledge.governance import (
    OFFICIAL_KIND,
    official_status_kind,
    unreadable_collections,
    withhold_official_request,
)
from workmind.modules.knowledge.models import Collection
from workmind.modules.knowledge.pipeline import set_document_labels
from workmind.modules.knowledge.service import collection_access
from workmind.modules.work.approvals import ApprovalKind, decide
from workmind.modules.work.links import task_link
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


def approval_kinds(resources: Resources) -> dict[str, ApprovalKind]:
    """Approvals the core carries out itself instead of running a tool."""

    async def trust_changed(tenant_id: uuid.UUID, document_id: uuid.UUID, trust: str) -> None:
        await set_document_labels(
            resources.qdrant, resources.collection_name, tenant_id, document_id, {"trust": trust}
        )

    return {OFFICIAL_KIND: official_status_kind(resources.db, trust_changed)}


def _approval(a: Approval, withheld: set[uuid.UUID] | None = None) -> ApprovalOut:
    arguments, summary = a.arguments, a.summary
    if a.tool == OFFICIAL_KIND and _collection_of(a) in (withheld or set()):
        arguments, summary = withhold_official_request(a.arguments)
    return ApprovalOut(
        id=a.id,
        tool=a.tool,
        summary=summary,
        arguments=arguments,
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


def _collection_of(approval: Approval) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(approval.arguments.get("collection_id")))
    except ValueError:
        return None


async def _withheld(
    session: AsyncSession, ctx: RequestContext, approvals: list[Approval]
) -> set[uuid.UUID]:
    """Collections of official-status requests the caller cannot open: those requests are shown
    without the document's title and the requester's note (docs/governance.md)."""
    ids = {c for a in approvals if a.tool == OFFICIAL_KIND and (c := _collection_of(a))}
    return await unreadable_collections(session, ctx, ids)


@router.get("/approvals")
async def list_approvals(
    ctx: AskContext,
    request: Request,
    status: str = "pending",
    conversation_id: Annotated[
        uuid.UUID | None, Query(description="only those proposed in this conversation")
    ] = None,
    workflow_run_id: Annotated[
        uuid.UUID | None, Query(description="only those this workflow run proposed")
    ] = None,
) -> list[ApprovalOut]:
    """Waiting for me (as approver), plus what I asked for (as requester), newest first (100).
    The filters narrow that set; they never show anyone else's approvals."""
    query = select(Approval).where(
        or_(Approval.approver_id == ctx.user_id, Approval.requested_by == ctx.user_id)
    )
    if status != "all":
        query = query.where(Approval.status == status)
    if conversation_id is not None:
        query = query.where(Approval.origin["conversation_id"].astext == str(conversation_id))
    if workflow_run_id is not None:
        query = query.where(Approval.origin["workflow_run_id"].astext == str(workflow_run_id))
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        rows = list(
            (await session.scalars(query.order_by(Approval.created_at.desc()).limit(100))).all()
        )
        withheld = await _withheld(session, ctx, rows)
    return [_approval(a, withheld) for a in rows]


@router.get("/approvals/{approval_id}")
async def get_approval(ctx: AskContext, request: Request, approval_id: uuid.UUID) -> ApprovalOut:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        approval = await session.get(Approval, approval_id)
        if approval is None or ctx.user_id not in (approval.approver_id, approval.requested_by):
            raise ApiError(404, "not_found", "Approval not found")
        withheld = await _withheld(session, ctx, [approval])
    return _approval(approval, withheld)


@router.post("/approvals/{approval_id}/decision")
async def decide_approval(
    ctx: AskContext, request: Request, approval_id: uuid.UUID, body: Decision
) -> ApprovalOut:
    resources = resources_of(request)
    kinds = approval_kinds(resources)
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        approval = await session.get(Approval, approval_id)
    if approval is not None and approval.tool in kinds and ctx.auth_method != "session":
        # Governance decisions (making a document official) need a browser session, like
        # every other governance change: an access token never makes one.
        raise ApiError(
            403,
            "session_required",
            "Decide this in the web app",
            "Access tokens cannot decide governance requests.",
        )
    if body.arguments is not None:  # an edit must still fit the tool
        if approval is not None and approval.tool in kinds:
            raise ApiError(422, "not_editable", "This request is approved or rejected as it is")
        tool = resources.tools.get(approval.tool) if approval else None
        if tool is not None:
            try:
                tool.validate(body.arguments)
            except (ValueError, ValidationError) as exc:
                raise ApiError(
                    422, "invalid_input", "The edited action is not valid", str(exc)[:500]
                ) from exc
    deps = WorkflowDeps(resources.db, resources.llm, resources.tools, resources.models)
    decided = await decide(
        resources.db,
        resources.tools,
        ctx,
        approval_id,
        decision=body.decision,
        edited_arguments=body.arguments,
        note=body.note,
        on_decided=lambda a: _resume_in_background(request, deps, a),
        kinds=kinds,
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
    """A guest may only make tasks for themselves: other people's lists and department queues
    are for members (a guest cannot see the directory either)."""
    if ctx.org_role == "guest" and (body.department or body.assignee_id not in (None, ctx.user_id)):
        raise ApiError(403, "forbidden", "Guests can only create tasks for themselves")
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
                link=task_link(task.id),
            )
        return _task(task)


async def _visible_task(session: AsyncSession, ctx: RequestContext, task_id: uuid.UUID) -> Task:
    """Assignee, creator, or a member of the task's department queue; others get a 404."""
    task = await session.get(Task, task_id)
    mine = task is not None and (
        ctx.user_id in (task.assignee_id, task.created_by)
        or (task.department_id in await _my_departments(session, ctx.user_id))
    )
    if task is None or not mine:
        raise ApiError(404, "not_found", "Task not found")
    return task


@router.get("/tasks/{task_id}")
async def get_task(ctx: AskContext, request: Request, task_id: uuid.UUID) -> TaskOut:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        return _task(await _visible_task(session, ctx, task_id))


@router.patch("/tasks/{task_id}")
async def update_task(
    ctx: AskContext, request: Request, task_id: uuid.UUID, body: TaskUpdate
) -> TaskOut:
    if ctx.org_role == "guest" and body.assignee_id not in (None, ctx.user_id):
        # As in create_task: a guest's tasks are for themselves, so they cannot hand one over
        # either (that would put work, and a notification title, on any employee's list).
        raise ApiError(403, "forbidden", "Guests can only assign tasks to themselves")
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        task = await _visible_task(session, ctx, task_id)
        if body.status:
            task.status = body.status
        if body.assignee_id and body.assignee_id != task.assignee_id:
            # Claiming from a queue, or handing over (the new assignee is told).
            if await session.get(User, body.assignee_id) is None:
                raise ApiError(422, "unknown_user", "Unknown assignee")
            task.assignee_id = body.assignee_id
            if body.assignee_id != ctx.user_id:
                await notify(
                    session,
                    ctx.tenant_id,
                    body.assignee_id,
                    kind="task.assigned",
                    title=f"Handed over to you: {task.title}",
                    link=task_link(task.id),
                )
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


async def _check_event_collection(
    session: AsyncSession, ctx: RequestContext, definition: dict[str, Any]
) -> None:
    """An event trigger's collection must exist, and the admin saving it must be able to view it:
    runs act as them, so a collection they can't view would never start one."""
    trigger: dict[str, Any] = definition.get("trigger") or {}
    key = trigger.get("collection")
    if not isinstance(key, str) or not key:
        return
    collection_id = await session.scalar(select(Collection.id).where(Collection.key == key))
    try:
        if collection_id is None:
            raise ApiError(404, "not_found", "Collection not found")
        await collection_access(session, ctx, collection_id)
    except ApiError as exc:
        raise ApiError(
            422,
            "trigger_collection",
            f"No collection “{key}” that you can view",
            "The workflow runs as you, so it only starts for documents you can view.",
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
        await _check_event_collection(session, ctx, definition)
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


@admin_router.delete("/{key}", status_code=204)
async def delete_workflow(ctx: AdminContext, request: Request, key: str) -> Response:
    """Deletes the workflow and its run history. Refused while a run is queued, running or
    waiting for an approval: its approval could still run the action after the workflow is gone.
    Disable the workflow to stop new runs meanwhile."""
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        workflow = await session.scalar(
            select(Workflow).where(Workflow.key == key).with_for_update()
        )
        if workflow is None:
            raise ApiError(404, "not_found", "Workflow not found")
        active = await session.scalar(
            select(func.count())
            .select_from(WorkflowRun)
            .where(
                WorkflowRun.workflow_id == workflow.id,
                WorkflowRun.status.in_(("queued", "running", "waiting_approval")),
            )
        )
        if active:
            raise ApiError(
                409,
                "runs_in_progress",
                "This workflow has runs in progress",
                "Wait for them to finish, or reject the approvals they wait for, then delete it. "
                "Disable it to stop new runs meanwhile.",
            )
        runs = await session.scalar(
            select(func.count())
            .select_from(WorkflowRun)
            .where(WorkflowRun.workflow_id == workflow.id)
        )
        await session.delete(workflow)  # its runs go with it (ON DELETE CASCADE)
        await record_event(
            session,
            tenant_id=ctx.tenant_id,
            actor=Actor(type="user", id=ctx.user_id),
            action="admin.workflow.deleted",
            resource_type="workflow",
            resource_id=key,
            details={"name": workflow.name, "runs": runs or 0},
        )
    return Response(status_code=204)


@admin_router.post("/{key}/run", status_code=202, dependencies=[rate_limit("agent_test")])
async def run_workflow(ctx: AdminContext, request: Request, key: str) -> RunOut:
    resources = resources_of(request)
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        workflow = await session.scalar(select(Workflow).where(Workflow.key == key))
        if workflow is None:
            raise ApiError(404, "not_found", "Workflow not found")
        workflow_id = workflow.id
    deps = WorkflowDeps(resources.db, resources.llm, resources.tools, resources.models)
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
