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
from typing import Any

import httpx
from celery import Celery

from workmind.core.config import get_settings
from workmind.core.db import create_engine, create_session_factory
from workmind.modules.knowledge.pipeline import IngestionDeps, PermanentIngestionError, ingest
from workmind.modules.knowledge.storage import LocalObjectStorage
from workmind.modules.retrieval.qdrant import QdrantClient, QdrantError
from workmind.providers.embeddings import EmbeddingUnavailableError
from workmind.providers.factory import create_embeddings

INGEST_TASK = "workmind.ingest_document"

settings = get_settings()
app = Celery("workmind", broker=settings.celery_broker_url)
app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    task_acks_late=True,  # a crashed worker's task is redelivered
    worker_prefetch_multiplier=1,
    task_default_queue="default",
    task_routes={INGEST_TASK: {"queue": "ingest"}},
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
