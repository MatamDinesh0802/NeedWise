# Celery ships without type information.
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUntypedFunctionDecorator=false, reportUnknownArgumentType=false
"""Background workers (Celery on Valkey). Run with:

    celery -A workmind.workers.celery_app worker -Q ingest,default --concurrency 2

Tasks are thin: they build their own clients and call the same async code the API uses.
Transient failures (embedding server, Qdrant, network) retry with exponential backoff;
unreadable files fail permanently and show their reason in the Knowledge Hub.
"""

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from celery import Celery
from sqlalchemy import select

from workmind.core.config import get_settings
from workmind.core.db import create_engine, create_session_factory
from workmind.core.resources import Resources
from workmind.core.tenancy import global_transaction
from workmind.modules.connectors.registry import build_connector
from workmind.modules.connectors.schedule import due_sources
from workmind.modules.connectors.secrets import SecretBox
from workmind.modules.connectors.sync import SyncDeps, sync_source
from workmind.modules.identity.models import Tenant
from workmind.modules.knowledge.dispatch import CeleryDispatcher
from workmind.modules.knowledge.pipeline import IngestionDeps, PermanentIngestionError, ingest
from workmind.modules.knowledge.storage import LocalObjectStorage
from workmind.modules.meetings.pipeline import MeetingDeps, process_meeting
from workmind.modules.retrieval.qdrant import QdrantClient, QdrantError
from workmind.modules.work.approvals import expire_due
from workmind.modules.work.workflows import WorkflowDeps, due_workflows, resume_after, start_run
from workmind.providers.embeddings import EmbeddingUnavailableError
from workmind.providers.factory import create_embeddings, secret

INGEST_TASK = "workmind.ingest_document"
SYNC_TASK = "workmind.sync_source"
SCHEDULE_TASK = "workmind.sync_due_sources"
WORKFLOWS_TASK = "workmind.run_due_workflows"
EXPIRE_TASK = "workmind.expire_approvals"
MEETING_TASK = "workmind.process_meeting"

settings = get_settings()
app = Celery("workmind", broker=settings.celery_broker_url)
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
    },
    broker_connection_retry_on_startup=True,
    task_ignore_result=True,
)


async def _run_ingest(tenant_id: str, document_id: str, version_id: str) -> dict[str, Any]:
    engine = create_engine(settings.database_url)
    async with httpx.AsyncClient(timeout=120) as http:
        deps = IngestionDeps(
            sessions=create_session_factory(engine),
            storage=LocalObjectStorage(settings.object_storage_path),
            embeddings=create_embeddings(settings, http),
            qdrant=QdrantClient(http, settings.qdrant_url),
            collection_name=settings.qdrant_collection,
        )
        try:
            result = await ingest(
                deps, uuid.UUID(tenant_id), uuid.UUID(document_id), uuid.UUID(version_id)
            )
        finally:
            await engine.dispose()
    return {"document_id": document_id, "chunks": result.chunks}


@app.task(
    name=INGEST_TASK,
    autoretry_for=(EmbeddingUnavailableError, QdrantError, httpx.HTTPError, OSError),
    retry_backoff=True,
    retry_backoff_max=300,
    max_retries=8,
    dont_autoretry_for=(PermanentIngestionError,),
)
def ingest_document(tenant_id: str, document_id: str, version_id: str) -> dict[str, Any] | None:
    try:
        return asyncio.run(_run_ingest(tenant_id, document_id, version_id))
    except PermanentIngestionError:
        return None  # already recorded on the document; retrying cannot help


async def _run_sync(tenant_id: str, source_id: str) -> dict[str, Any]:
    engine = create_engine(settings.database_url)
    async with httpx.AsyncClient(timeout=60) as http:
        deps = SyncDeps(
            sessions=create_session_factory(engine),
            storage=LocalObjectStorage(settings.object_storage_path),
            ingestion=CeleryDispatcher(),  # each new version is ingested as its own task
            qdrant=QdrantClient(http, settings.qdrant_url),
            collection_name=settings.qdrant_collection,
            secrets=SecretBox(settings.secrets_key.get_secret_value()),
            connectors=lambda source, credentials: build_connector(
                source,
                credentials,
                http=http,
                google_client=(settings.google_client_id, secret(settings.google_client_secret)),
            ),
            max_bytes=settings.max_upload_bytes,
        )
        try:
            report = await sync_source(deps, uuid.UUID(tenant_id), uuid.UUID(source_id))
        finally:
            await engine.dispose()
    return {"source_id": source_id, "status": report.status, **report.stats()}


@app.task(name=SYNC_TASK)
def sync_source_task(tenant_id: str, source_id: str) -> dict[str, Any]:
    """Failures are recorded on the source; the next interval retries."""
    return asyncio.run(_run_sync(tenant_id, source_id))


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
    resources = Resources.create(settings)
    await resources.load_remote_tools()
    started = 0
    try:
        deps = WorkflowDeps(resources.db, resources.llm, resources.tools, resources.models)
        now = datetime.now(UTC)
        for tenant_id in await _active_tenants(resources):
            for workflow_id in await due_workflows(resources.db, tenant_id, now):
                await start_run(deps, tenant_id, workflow_id, trigger="schedule")
                started += 1
    finally:
        await resources.aclose()
    return started


@app.task(name=WORKFLOWS_TASK)
def run_due_workflows() -> int:
    return asyncio.run(_run_due_workflows())


async def _expire_approvals() -> int:
    resources = Resources.create(settings)
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


async def _process_meeting(tenant_id: str, meeting_id: str) -> None:
    resources = Resources.create(settings)
    try:
        deps = MeetingDeps(
            resources.db,
            resources.storage,
            await resources.models.for_tenant(uuid.UUID(tenant_id)),
            resources.transcriber,
            CeleryDispatcher(),
        )
        await process_meeting(deps, uuid.UUID(tenant_id), uuid.UUID(meeting_id))
    finally:
        await resources.aclose()


@app.task(name=MEETING_TASK)
def process_meeting_task(tenant_id: str, meeting_id: str) -> None:
    """Failures are recorded on the meeting (status "failed" with the reason)."""
    asyncio.run(_process_meeting(tenant_id, meeting_id))
