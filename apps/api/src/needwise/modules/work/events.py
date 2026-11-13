"""Event triggers (docs/automation.md): something happened, so the workflows waiting for it run.

    document.ready      a document version finished indexing (ingestion, same transaction)
    document.updated    the same, for a version after the first: the content changed
    meeting.processed   a meeting's notes are ready (the meetings pipeline, same transaction)
    task.completed      a task was marked done (the tasks API, same transaction)
    approval.decided    an approval's decision is final: executed, failed or rejected

      -> one *queued* run per matching workflow, keyed by the event
      -> whoever caused the event starts the runs (start_queued in workflows.py); the beat
         task starts any that nobody started (a crash between the commit and the start)

Exactly once per event and workflow: the run's idempotency key is unique, so a repeated or
re-delivered event finds the run already there.

A run acts as the workflow's owner, with their current access, and only for something the
owner could open themselves: the document's or meeting's collection (the Knowledge Hub's rule)
and, for a document with its own access list, that list too; the task (as its assignee,
creator, a member of its queue, or a triager); the approval (as its approver or requester).
Otherwise no run is created and the event tells the owner nothing.
"""

import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Final

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from needwise.modules.governance.audit import Actor, record_event
from needwise.modules.identity.models import Department, User
from needwise.modules.identity.service import Identity, load_identity
from needwise.modules.knowledge.acl import acl_principals, can_view_document
from needwise.modules.knowledge.models import Collection, CollectionGrant, Document, DocumentGrant
from needwise.modules.work.links import approval_link, meeting_link, task_link
from needwise.modules.work.models import Approval, Task, Workflow, WorkflowRun
from needwise.modules.work.visibility import can_see_task

log = structlog.get_logger("needwise.workflows")

DOCUMENT_READY: Final = "document.ready"
DOCUMENT_UPDATED: Final = "document.updated"
MEETING_PROCESSED: Final = "meeting.processed"
TASK_COMPLETED: Final = "task.completed"
APPROVAL_DECIDED: Final = "approval.decided"
EVENTS: Final = (
    DOCUMENT_READY,
    DOCUMENT_UPDATED,
    MEETING_PROCESSED,
    TASK_COMPLETED,
    APPROVAL_DECIDED,
)
# Which trigger filter each event understands (the workflow definition is checked against it).
FILTERS: Final[dict[str, str | None]] = {
    DOCUMENT_READY: "collection",
    DOCUMENT_UPDATED: "collection",
    MEETING_PROCESSED: "collection",
    TASK_COMPLETED: "department",
    APPROVAL_DECIDED: "tool",
}
DECIDED_STATUSES: Final = ("executed", "failed", "rejected")


@dataclass(frozen=True)
class DocumentReady:
    """A document version that was just indexed. `payload()` is what `{{ event.<key> }}` reads."""

    document_id: uuid.UUID
    version_id: uuid.UUID
    version: int
    collection_id: uuid.UUID
    title: str
    filename: str
    source: str
    url: str | None = None

    def key(self, event: str = DOCUMENT_READY) -> str:
        return f"{event}:{self.version_id}"

    def payload(self, collection: Collection, event: str = DOCUMENT_READY) -> dict[str, Any]:
        return {
            "name": event,
            "document_id": str(self.document_id),
            "version_id": str(self.version_id),
            "version": self.version,
            "title": self.title,
            "filename": self.filename,
            "source": self.source,
            "url": self.url or "",
            "link": f"/knowledge/documents/{self.document_id}",
            "collection": collection.key,
            "collection_name": collection.name,
        }


def listens_to(definition: dict[str, Any], event: str, value: str | None = None) -> bool:
    """Whether a workflow's trigger matches: the event, and its filter (collection, department
    or tool) if it has one."""
    trigger: dict[str, Any] = definition.get("trigger") or {}
    if trigger.get("event") != event:
        return False
    name = FILTERS.get(event)
    wanted = trigger.get(name) if name else None
    return not wanted or wanted == value


Viewer = Callable[[Identity], Awaitable[bool]]


async def _queue(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    event: str,
    key: str,
    payload: dict[str, Any],
    filter_value: str | None,
    may_see: Viewer,
    details: dict[str, Any],
    skip_workflow: str | None = None,
) -> list[uuid.UUID]:
    """Queues a run of every enabled workflow listening for this event whose owner may see what
    it is about (inside the caller's transaction). Returns the runs created now."""
    workflows = (
        await session.scalars(
            select(Workflow).where(
                Workflow.enabled.is_(True),
                Workflow.definition["trigger"]["event"].astext == event,
            )
        )
    ).all()
    created: list[uuid.UUID] = []
    for workflow in workflows:
        if workflow.key == skip_workflow:
            continue  # a workflow's own approvals never trigger it again (no loops)
        if not listens_to(workflow.definition, event, filter_value):
            continue
        owner = await load_identity(session, workflow.owner_id)
        if owner is None or owner.user.status != "active" or not await may_see(owner):
            log.info("workflow_event_skipped", workflow=workflow.key, reason="owner_cannot_view")
            continue
        taken = await session.scalar(
            select(WorkflowRun.id).where(
                WorkflowRun.workflow_id == workflow.id, WorkflowRun.idempotency_key == key
            )
        )
        if taken is not None:
            # A repeated event. Checked first because the insert below would wait for a run
            # that is finishing right now (its row is locked) while this transaction may hold
            # the audit log's lock that the run needs: a deadlock.
            continue
        run_id = await session.scalar(
            insert(WorkflowRun)
            .values(
                tenant_id=tenant_id,
                workflow_id=workflow.id,
                status="queued",
                trigger="event",
                idempotency_key=key,
                steps={"_event": payload},
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "workflow_id", "idempotency_key"])
            .returning(WorkflowRun.id)
        )
        if run_id is None:
            continue  # this event already started a run of this workflow
        created.append(run_id)
        await record_event(
            session,
            tenant_id=tenant_id,
            actor=Actor.system(),
            action="workflow.triggered",
            resource_type="workflow",
            resource_id=workflow.key,
            details={"run_id": str(run_id), "event": event, **details},
        )
    return created


async def _has_listeners(session: AsyncSession, event: str) -> bool:
    return (
        await session.scalar(
            select(Workflow.id)
            .where(
                Workflow.enabled.is_(True),
                Workflow.definition["trigger"]["event"].astext == event,
            )
            .limit(1)
        )
    ) is not None


async def _collection_viewer(
    session: AsyncSession, collection: Collection, document: Document | None = None
) -> Viewer:
    grants = (
        await session.scalars(
            select(CollectionGrant).where(CollectionGrant.collection_id == collection.id)
        )
    ).all()
    viewers = set(acl_principals(collection, grants))
    document_grants: Sequence[DocumentGrant] = []
    if document is not None and document.own_acl:
        document_grants = (
            await session.scalars(
                select(DocumentGrant).where(DocumentGrant.document_id == document.id)
            )
        ).all()

    async def may_see(owner: Identity) -> bool:
        # Same rule as the Knowledge Hub and search (lock 1): no access, no run. A document
        # with its own access list (docs/permission-sync.md) must name the owner too.
        if not viewers & owner.principals:
            return False
        return document is None or can_view_document(owner.principals, document, document_grants)

    return may_see


async def document_ready(
    session: AsyncSession, tenant_id: uuid.UUID, event: DocumentReady
) -> list[uuid.UUID]:
    """document.ready for every version; document.updated too for a version after the first."""
    names = [DOCUMENT_READY] + ([DOCUMENT_UPDATED] if event.version > 1 else [])
    names = [n for n in names if await _has_listeners(session, n)]
    collection = await session.get(Collection, event.collection_id)
    document = await session.get(Document, event.document_id)
    if not names or collection is None or document is None:
        return []
    may_see = await _collection_viewer(session, collection, document)
    created: list[uuid.UUID] = []
    for name in names:
        created += await _queue(
            session,
            tenant_id,
            event=name,
            key=event.key(name),
            payload=event.payload(collection, name),
            filter_value=collection.key,
            may_see=may_see,
            details={
                "document_id": str(event.document_id),
                "version_id": str(event.version_id),
                "collection": collection.key,
            },
        )
    return created


async def meeting_processed(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    meeting_id: uuid.UUID,
    title: str,
    collection_id: uuid.UUID,
    summary: str,
    decisions: int,
    action_items: int,
) -> list[uuid.UUID]:
    if not await _has_listeners(session, MEETING_PROCESSED):
        return []
    collection = await session.get(Collection, collection_id)
    if collection is None:
        return []
    return await _queue(
        session,
        tenant_id,
        event=MEETING_PROCESSED,
        key=f"{MEETING_PROCESSED}:{meeting_id}",
        payload={
            "name": MEETING_PROCESSED,
            "meeting_id": str(meeting_id),
            "title": title,
            "summary": summary,
            "decisions": decisions,
            "action_items": action_items,
            "link": meeting_link(meeting_id),
            "collection": collection.key,
            "collection_name": collection.name,
        },
        filter_value=collection.key,
        may_see=await _collection_viewer(session, collection),
        details={"meeting_id": str(meeting_id), "collection": collection.key},
    )


async def _email(session: AsyncSession, user_id: uuid.UUID | None) -> str:
    if user_id is None:
        return ""
    return await session.scalar(select(User.email).where(User.id == user_id)) or ""


async def task_completed(
    session: AsyncSession, tenant_id: uuid.UUID, task: Task, completed_by: uuid.UUID
) -> list[uuid.UUID]:
    """Once per task: a task reopened and completed again does not trigger again."""
    if not await _has_listeners(session, TASK_COMPLETED):
        return []
    department = (
        await session.scalar(select(Department.slug).where(Department.id == task.department_id))
        if task.department_id
        else None
    )

    async def may_see(owner: Identity) -> bool:
        return await can_see_task(session, owner.user.id, owner.user.org_role, task)

    return await _queue(
        session,
        tenant_id,
        event=TASK_COMPLETED,
        key=f"{TASK_COMPLETED}:{task.id}",
        payload={
            "name": TASK_COMPLETED,
            "task_id": str(task.id),
            "title": task.title,
            "description": task.description,
            "assignee": await _email(session, task.assignee_id),
            "department": department or "",
            "completed_by": await _email(session, completed_by),
            "link": task_link(task.id),
        },
        filter_value=department,
        may_see=may_see,
        details={"task_id": str(task.id), "department": department},
    )


async def approval_decided(
    session: AsyncSession, tenant_id: uuid.UUID, approval: Approval
) -> list[uuid.UUID]:
    """A final decision: executed or failed (approved), or rejected. Not a chain's forwarded
    stage, not a copy another approver's decision superseded, not an expiry."""
    if approval.status not in DECIDED_STATUSES or not await _has_listeners(
        session, APPROVAL_DECIDED
    ):
        return []

    async def may_see(owner: Identity) -> bool:
        return owner.user.id in (approval.approver_id, approval.requested_by)

    return await _queue(
        session,
        tenant_id,
        event=APPROVAL_DECIDED,
        key=f"{APPROVAL_DECIDED}:{approval.id}",
        payload={
            "name": APPROVAL_DECIDED,
            "approval_id": str(approval.id),
            "tool": approval.tool,
            "decision": "rejected" if approval.status == "rejected" else "approved",
            "status": approval.status,
            "summary": approval.summary,
            "note": approval.decision_note or "",
            "requested_by": await _email(session, approval.requested_by),
            "decided_by": await _email(session, approval.decided_by),
            "link": approval_link(approval.id),
        },
        filter_value=approval.tool,
        may_see=may_see,
        details={"approval_id": str(approval.id), "tool": approval.tool},
        skip_workflow=str(approval.origin.get("workflow") or "") or None,
    )
