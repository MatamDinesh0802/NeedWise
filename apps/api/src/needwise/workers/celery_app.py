# Celery ships without type information.
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUntypedFunctionDecorator=false, reportUnknownArgumentType=false
"""Background workers (Celery on Valkey). Run with:

    celery -A needwise.workers.celery_app worker -Q ingest,default --concurrency 2

Tasks are thin: they build their own clients and call the same async code the API uses.
Transient failures (embedding server, Qdrant, network) retry with exponential backoff;
unreadable files fail permanently and show their reason in the Knowledge Hub.

Every task about one tenant's data takes the tenant id as its first argument and does all its
work inside that tenant (Row-Level Security, tenant-scoped Qdrant and storage keys), so another
tenant's record id finds nothing. A missing or malformed tenant id is refused
(TenantRequiredError, never retried); a task for a suspended or deleted tenant does nothing.
"""

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog
from celery import Celery
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from needwise.core.config import get_settings
from needwise.core.db import create_engine, create_session_factory
from needwise.core.extensions import load_extensions
from needwise.core.resources import Resources
from needwise.core.tenancy import global_transaction
from needwise.modules.connectors.oauth import OAuthApps
from needwise.modules.connectors.registry import build_connector
from needwise.modules.connectors.reminders import send_source_reminders
from needwise.modules.connectors.schedule import due_sources
from needwise.modules.connectors.secrets import SecretBox
from needwise.modules.connectors.sync import SyncDeps, sync_source
from needwise.modules.identity.models import Tenant
from needwise.modules.identity.service import tenant_is_active
from needwise.modules.knowledge.dispatch import CeleryDispatcher
from needwise.modules.knowledge.governance import escalate_overdue_reviews, send_review_reminders
from needwise.modules.knowledge.pipeline import (
    IngestionDeps,
    PermanentIngestionError,
    ingest,
    set_document_labels,
)
from needwise.modules.knowledge.storage import LocalObjectStorage
from needwise.modules.meetings.pipeline import MeetingDeps, process_meeting
from needwise.modules.model_catalog.secrets import KeyRing
from needwise.modules.retrieval.qdrant import QdrantClient, QdrantError
from needwise.modules.work.approvals import expire_due
from needwise.modules.work.delivery import DeliveryDeps, deliver_due, queue_digests
from needwise.modules.work.escalation import run_escalation
from needwise.modules.work.slack import SlackClient
from needwise.modules.work.workflows import (
    WorkflowDeps,
    due_workflows,
    queued_runs,
    resume_after,
    start_queued,
    start_run,
)
from needwise.providers.embeddings import EmbeddingUnavailableError
from needwise.providers.factory import create_embeddings, secret
from needwise.workers.enterprise import register_scheduled_tasks

INGEST_TASK = "needwise.ingest_document"
SYNC_TASK = "needwise.sync_source"
SCHEDULE_TASK = "needwise.sync_due_sources"
WORKFLOWS_TASK = "needwise.run_due_workflows"
EXPIRE_TASK = "needwise.expire_approvals"
MEETING_TASK = "needwise.process_meeting"
WORKFLOW_RUN_TASK = "needwise.start_workflow_run"
REVIEWS_TASK = "needwise.send_review_reminders"
SOURCE_REMINDERS_TASK = "needwise.send_source_reminders"
ESCALATE_TASK = "needwise.escalate_work"
DELIVER_TASK = "needwise.deliver_notifications"

log = structlog.get_logger("needwise.workers")
settings = get_settings()
app = Celery("needwise", broker=settings.celery_broker_url)
app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    task_acks_late=True,  # a crashed worker's task is redelivered
    worker_prefetch_multiplier=1,
    task_default_queue="default",
    task_routes={
        INGEST_TASK: {"queue": "ingest"},
        MEETING_TASK: {"queue": "ingest"},
        SYNC_TASK: {"queue": "default"},
    },
    # `celery beat` (one process) checks every minute which sources are due.
    beat_schedule={
        "sync-due-sources": {"task": SCHEDULE_TASK, "schedule": 60.0},
        "run-due-workflows": {"task": WORKFLOWS_TASK, "schedule": 60.0},
        "expire-approvals": {"task": EXPIRE_TASK, "schedule": 300.0},
        # Daily: documents past their review date get one reminder per review cycle, and
        # official ones still overdue escalate one stage (docs/governance.md).
        "review-reminders": {"task": REVIEWS_TASK, "schedule": 86_400.0},
        # Daily: stale synced documents (once per staleness episode), failing sources.
        "source-reminders": {"task": SOURCE_REMINDERS_TASK, "schedule": 86_400.0},
        # Hourly: approval and task reminders and escalations, once each (docs/automation.md).
        "escalate-work": {"task": ESCALATE_TASK, "schedule": 3_600.0},
        # Every minute: digests at each person's hour, then the email and Slack outbox.
        "deliver-notifications": {"task": DELIVER_TASK, "schedule": 60.0},
    },
    broker_connection_retry_on_startup=True,
    task_ignore_result=True,
)
# Community, or Enterprise with what needwise_ee registered (ADR 0011). Built when the worker
# or beat imports this module, so beat schedules the enterprise tasks and workers know them.
extensions = load_extensions(settings)


class TenantRequiredError(ValueError):
    """A tenant task called without a valid tenant id. Never retried: it cannot succeed."""


def tenant_of(value: object) -> uuid.UUID:
    """The tenant argument of a task, refused when missing or malformed."""
    if not isinstance(value, str | uuid.UUID) or not str(value).strip():
        raise TenantRequiredError("this task needs the id of the tenant it works for")
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(value)
    except ValueError as exc:
        raise TenantRequiredError(f"not a tenant id: {value!r}") from exc


async def _tenant_active(
    sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID, task: str
) -> bool:
    async with global_transaction(sessions) as session:
        active = await tenant_is_active(session, tenant_id)
    if not active:
        log.info("task_skipped_inactive_tenant", task=task, tenant_id=str(tenant_id))
    return active


async def _run_ingest(tenant_id: str, document_id: str, version_id: str) -> dict[str, Any] | None:
    tenant = tenant_of(tenant_id)
    engine = create_engine(settings.database_url)
    if not await _tenant_active(create_session_factory(engine), tenant, INGEST_TASK):
        await engine.dispose()
        return None
    async with httpx.AsyncClient(timeout=120) as http:
        deps = IngestionDeps(
            sessions=create_session_factory(engine),
            storage=LocalObjectStorage(settings.object_storage_path),
            embeddings=create_embeddings(settings, http),
            qdrant=QdrantClient(http, settings.qdrant_url, secret(settings.qdrant_api_key)),
            collection_name=settings.qdrant_collection,
        )
        try:
            result = await ingest(deps, tenant, uuid.UUID(document_id), uuid.UUID(version_id))
        finally:
            await engine.dispose()
    for run_id in result.workflow_runs:  # "document.ready": each triggered run is its own task
        app.send_task(WORKFLOW_RUN_TASK, args=[tenant_id, str(run_id)])
    return {"document_id": document_id, "chunks": result.chunks}


@app.task(
    name=INGEST_TASK,
    autoretry_for=(EmbeddingUnavailableError, QdrantError, httpx.HTTPError, OSError),
    retry_backoff=True,
    retry_backoff_max=300,
    max_retries=8,
    dont_autoretry_for=(PermanentIngestionError, TenantRequiredError),
)
def ingest_document(tenant_id: str, document_id: str, version_id: str) -> dict[str, Any] | None:
    try:
        return asyncio.run(_run_ingest(tenant_id, document_id, version_id))
    except PermanentIngestionError:
        return None  # already recorded on the document; retrying cannot help


async def _run_sync(
    tenant_id: str, source_id: str, trigger: str, requested_by: str | None
) -> dict[str, Any]:
    tenant = tenant_of(tenant_id)
    engine = create_engine(settings.database_url)
    if not await _tenant_active(create_session_factory(engine), tenant, SYNC_TASK):
        await engine.dispose()
        return {"source_id": source_id, "status": "skipped"}
    async with httpx.AsyncClient(timeout=60) as http:
        deps = SyncDeps(
            sessions=create_session_factory(engine),
            storage=LocalObjectStorage(settings.object_storage_path),
            ingestion=CeleryDispatcher(),  # each new version is ingested as its own task
            qdrant=QdrantClient(http, settings.qdrant_url, secret(settings.qdrant_api_key)),
            collection_name=settings.qdrant_collection,
            secrets=SecretBox(settings.secrets_key.get_secret_value()),
            connectors=lambda source, credentials: build_connector(
                source, credentials, http=http, apps=OAuthApps.from_settings(settings)
            ),
            max_bytes=settings.max_upload_bytes,
            failure_alert_after=settings.source_failure_alert_after,
            extensions=extensions,  # permission sync, when licensed (docs/permission-sync.md)
        )
        try:
            report = await sync_source(
                deps,
                tenant,
                uuid.UUID(source_id),
                trigger="manual" if trigger == "manual" else "schedule",
                requested_by=uuid.UUID(requested_by) if requested_by else None,
            )
        finally:
            await engine.dispose()
    return {"source_id": source_id, "status": report.status, **report.stats()}


@app.task(name=SYNC_TASK)
def sync_source_task(
    tenant_id: str, source_id: str, trigger: str = "schedule", requested_by: str | None = None
) -> dict[str, Any]:
    """Failures are recorded on the source and as a run; the next interval retries."""
    return asyncio.run(_run_sync(tenant_id, source_id, trigger, requested_by))


async def _due() -> list[tuple[uuid.UUID, uuid.UUID]]:
    engine = create_engine(settings.database_url)
    try:
        return await due_sources(create_session_factory(engine))
    finally:
        await engine.dispose()


@app.task(name=SCHEDULE_TASK)
def sync_due_sources() -> int:
    due = asyncio.run(_due())
    for tenant_id, source_id in due:
        app.send_task(SYNC_TASK, args=[str(tenant_id), str(source_id)])
    return len(due)


async def _active_tenants(resources: Resources) -> list[uuid.UUID]:
    async with global_transaction(resources.db) as session:
        return list(
            (await session.scalars(select(Tenant.id).where(Tenant.status == "active"))).all()
        )


async def _run_due_workflows() -> int:
    resources = Resources.create(settings, extensions=extensions)
    await resources.load_remote_tools()
    started = 0
    try:
        deps = WorkflowDeps(resources.db, resources.llm, resources.tools, resources.models)
        now = datetime.now(UTC)
        for tenant_id in await _active_tenants(resources):
            # Event runs nobody started (e.g. the worker stopped right after queueing them).
            for run_id in await queued_runs(resources.db, tenant_id, now):
                app.send_task(WORKFLOW_RUN_TASK, args=[str(tenant_id), str(run_id)])
            for workflow_id in await due_workflows(resources.db, tenant_id, now):
                await start_run(deps, tenant_id, workflow_id, trigger="schedule")
                started += 1
    finally:
        await resources.aclose()
    return started


@app.task(name=WORKFLOWS_TASK)
def run_due_workflows() -> int:
    return asyncio.run(_run_due_workflows())


async def _start_workflow_run(tenant_id: str, run_id: str) -> str | None:
    tenant = tenant_of(tenant_id)
    resources = Resources.create(settings, extensions=extensions)
    try:
        if not await _tenant_active(resources.db, tenant, WORKFLOW_RUN_TASK):
            return None
        await resources.load_remote_tools()
        deps = WorkflowDeps(resources.db, resources.llm, resources.tools, resources.models)
        run = await start_queued(deps, tenant, uuid.UUID(run_id))
    finally:
        await resources.aclose()
    return run.status if run else None


@app.task(name=WORKFLOW_RUN_TASK)
def start_workflow_run(tenant_id: str, run_id: str) -> str | None:
    """A queued (event) run. Already started elsewhere: nothing happens."""
    return asyncio.run(_start_workflow_run(tenant_id, run_id))


async def _expire_approvals() -> int:
    resources = Resources.create(settings, extensions=extensions)
    count = 0
    try:
        deps = WorkflowDeps(resources.db, resources.llm, resources.tools, resources.models)
        for tenant_id in await _active_tenants(resources):
            for approval in await expire_due(resources.db, tenant_id):
                await resume_after(deps, approval)  # cancels a workflow waiting on it
                count += 1
    finally:
        await resources.aclose()
    return count


@app.task(name=EXPIRE_TASK)
def expire_approvals() -> int:
    return asyncio.run(_expire_approvals())


async def review_tenant(resources: Resources, tenant_id: uuid.UUID) -> tuple[int, int]:
    """One tenant's daily review run: reminders, then escalation of overdue official documents.
    A document demoted by the escalation leaves official status in the search index at once."""
    report = await send_review_reminders(resources.db, tenant_id)
    escalated = await escalate_overdue_reviews(resources.db, tenant_id)
    for document_id in escalated.demoted:
        await set_document_labels(
            resources.qdrant,
            resources.collection_name,
            tenant_id,
            document_id,
            {"trust": "reference"},
        )
    moved = len(escalated.to_collection_owner) + len(escalated.to_admins) + len(escalated.demoted)
    return len(report.reminded), moved


async def _send_review_reminders() -> int:
    resources = Resources.create(settings, extensions=extensions)
    reminded = 0
    try:
        for tenant_id in await _active_tenants(resources):
            count, escalated = await review_tenant(resources, tenant_id)
            reminded += count
            if escalated:
                log.info("reviews_escalated", tenant_id=str(tenant_id), documents=escalated)
    finally:
        await resources.aclose()
    return reminded


@app.task(name=REVIEWS_TASK)
def review_reminders() -> int:
    """Idempotent: a document is reminded once per review cycle and escalates once per stage,
    however often this runs."""
    return asyncio.run(_send_review_reminders())


async def _send_source_reminders() -> int:
    resources = Resources.create(settings, extensions=extensions)
    reminded = 0
    try:
        for tenant_id in await _active_tenants(resources):
            report = await send_source_reminders(
                resources.db,
                tenant_id,
                failure_alert_after=settings.source_failure_alert_after,
            )
            reminded += len(report.reminded)
    finally:
        await resources.aclose()
    return reminded


@app.task(name=SOURCE_REMINDERS_TASK)
def source_reminders() -> int:
    """Idempotent: a stale document is reminded once per staleness episode, a failing source
    once per failure episode, however often this runs."""
    return asyncio.run(_send_source_reminders())


async def _process_meeting(tenant_id: str, meeting_id: str) -> None:
    tenant = tenant_of(tenant_id)
    resources = Resources.create(settings, extensions=extensions)
    try:
        if not await _tenant_active(resources.db, tenant, MEETING_TASK):
            return
        deps = MeetingDeps(
            resources.db,
            resources.storage,
            await resources.models.for_tenant(tenant),
            resources.transcriber,
            CeleryDispatcher(),
            resources.diarizer,
        )
        runs = await process_meeting(deps, tenant, uuid.UUID(meeting_id))
    finally:
        await resources.aclose()
    for run_id in runs:  # "meeting.processed": each triggered run is its own task
        app.send_task(WORKFLOW_RUN_TASK, args=[tenant_id, str(run_id)])


@app.task(name=MEETING_TASK)
def process_meeting_task(tenant_id: str, meeting_id: str) -> None:
    """Failures are recorded on the meeting (status "failed" with the reason)."""
    asyncio.run(_process_meeting(tenant_id, meeting_id))


async def _escalate_work() -> int:
    resources = Resources.create(settings, extensions=extensions)
    acted = 0
    try:
        now = datetime.now(UTC)
        for tenant_id in await _active_tenants(resources):
            report = await run_escalation(resources.db, tenant_id, now)
            acted += sum(vars(report).values())
    finally:
        await resources.aclose()
    return acted


@app.task(name=ESCALATE_TASK)
def escalate_work() -> int:
    """Idempotent: each reminder and escalation happens once per approval (stage) or task."""
    return asyncio.run(_escalate_work())


async def _deliver_notifications() -> int:
    resources = Resources.create(settings, extensions=extensions)
    sent = 0
    try:
        deps = DeliveryDeps(
            sessions=resources.db,
            mailer=resources.mailer,
            slack=SlackClient(resources.http, settings.slack_api_url),
            keyring=KeyRing.from_settings(settings),
            public_url=settings.public_url,
        )
        now = datetime.now(UTC)
        for tenant_id in await _active_tenants(resources):
            await queue_digests(resources.db, tenant_id, now)
            report = await deliver_due(deps, tenant_id, now)
            sent += report.sent
    finally:
        await resources.aclose()
    return sent


@app.task(name=DELIVER_TASK)
def deliver_notifications() -> int:
    """Failures are logged and retried with backoff; they never affect the action itself."""
    return asyncio.run(_deliver_notifications())


# Enterprise scheduled tasks (none in Community): known to workers, queued by beat.
register_scheduled_tasks(app, settings, extensions)
