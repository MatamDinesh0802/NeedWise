"""Event triggers (docs/automation.md): something happened, so the workflows waiting for it run.

    a document version becomes ready (ingestion, in the same transaction)
      -> document_ready(): one *queued* run per matching workflow, keyed by the version
      -> whoever ingested it starts the runs (start_queued in workflows.py); the beat task
         starts any that nobody started (a crash between the commit and the start)

Exactly once per document version and workflow: the run's idempotency key is unique, so a
re-delivered ingestion task or a re-index of the same version finds the run already there.

A run acts as the workflow's owner, with their current access. A workflow whose owner cannot
view the document (its collection, and its own access list if it has one) gets no run, so the
event never tells them about a document they could not open themselves.
"""

import uuid
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from workmind.modules.governance.audit import Actor, record_event
from workmind.modules.identity.service import load_identity
from workmind.modules.knowledge.acl import acl_principals, can_view_document
from workmind.modules.knowledge.models import Collection, CollectionGrant, Document, DocumentGrant
from workmind.modules.work.models import Workflow, WorkflowRun

log = structlog.get_logger("workmind.workflows")

DOCUMENT_READY = "document.ready"


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

    def key(self) -> str:
        return f"{DOCUMENT_READY}:{self.version_id}"

    def payload(self, collection: Collection) -> dict[str, Any]:
        return {
            "name": DOCUMENT_READY,
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


def listens_to(definition: dict[str, Any], event: str, collection_key: str) -> bool:
    """Whether a workflow's trigger matches: the event, and its collection filter if it has one."""
    trigger: dict[str, Any] = definition.get("trigger") or {}
    if trigger.get("event") != event:
        return False
    wanted = trigger.get("collection")
    return not wanted or wanted == collection_key


async def document_ready(
    session: AsyncSession, tenant_id: uuid.UUID, event: DocumentReady
) -> list[uuid.UUID]:
    """Queues a run of every enabled workflow listening for this event (inside the caller's
    transaction). Returns the runs created now; a repeated event creates none."""
    workflows = (
        await session.scalars(
            select(Workflow).where(
                Workflow.enabled.is_(True),
                Workflow.definition["trigger"]["event"].astext == DOCUMENT_READY,
            )
        )
    ).all()
    collection = await session.get(Collection, event.collection_id)
    if not workflows or collection is None:
        return []
    grants = (
        await session.scalars(
            select(CollectionGrant).where(CollectionGrant.collection_id == collection.id)
        )
    ).all()
    viewers = set(acl_principals(collection, grants))
    document = await session.get(Document, event.document_id)
    if document is None:
        return []
    document_grants = (
        await session.scalars(select(DocumentGrant).where(DocumentGrant.document_id == document.id))
    ).all()
    payload = event.payload(collection)
    created: list[uuid.UUID] = []
    for workflow in workflows:
        if not listens_to(workflow.definition, DOCUMENT_READY, collection.key):
            continue
        owner = await load_identity(session, workflow.owner_id)
        if (
            owner is None
            or not viewers & owner.principals
            or not can_view_document(owner.principals, document, document_grants)
        ):
            # Same rule as the Knowledge Hub and search (lock 1): no access, no run.
            log.info("workflow_event_skipped", workflow=workflow.key, reason="owner_cannot_view")
            continue
        run_id = await session.scalar(
            insert(WorkflowRun)
            .values(
                tenant_id=tenant_id,
                workflow_id=workflow.id,
                status="queued",
                trigger="event",
                idempotency_key=event.key(),
                steps={"_event": payload},
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "workflow_id", "idempotency_key"])
            .returning(WorkflowRun.id)
        )
        if run_id is None:
            continue  # this version already started a run of this workflow
        created.append(run_id)
        await record_event(
            session,
            tenant_id=tenant_id,
            actor=Actor.system(),
            action="workflow.triggered",
            resource_type="workflow",
            resource_id=workflow.key,
            details={
                "run_id": str(run_id),
                "event": DOCUMENT_READY,
                "document_id": str(event.document_id),
                "version_id": str(event.version_id),
                "collection": collection.key,
            },
        )
    return created
