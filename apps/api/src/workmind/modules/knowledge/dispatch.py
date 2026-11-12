"""Hands a new document version to ingestion: a Celery worker in normal deployments, or an
in-process task ("inline") for tests and single-process setups."""

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol

import structlog

from workmind.modules.knowledge.pipeline import IngestionDeps, PermanentIngestionError, ingest

log = structlog.get_logger("workmind.ingestion")

# Starts the workflow runs a ready document queued ("document.ready"), in the background.
StartRuns = Callable[[uuid.UUID, Sequence[uuid.UUID]], Awaitable[None]]


class Dispatcher(Protocol):
    async def dispatch(
        self, tenant_id: uuid.UUID, document_id: uuid.UUID, version_id: uuid.UUID
    ) -> None: ...


class CeleryDispatcher:
    async def dispatch(
        self, tenant_id: uuid.UUID, document_id: uuid.UUID, version_id: uuid.UUID
    ) -> None:
        # Imported lazily: only Celery deployments need the worker module.
        from workmind.workers.celery_app import INGEST_TASK, app

        args = [str(tenant_id), str(document_id), str(version_id)]
        await asyncio.to_thread(app.send_task, INGEST_TASK, args=args)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]


class InlineDispatcher:
    def __init__(self, deps: IngestionDeps, start_runs: StartRuns | None = None) -> None:
        self._deps = deps
        # Set by the process once it can run workflows (it needs the model and the tools).
        # Without it, queued runs wait for the beat task.
        self.start_runs = start_runs
        self.tasks: set[asyncio.Task[None]] = set()

    async def dispatch(
        self, tenant_id: uuid.UUID, document_id: uuid.UUID, version_id: uuid.UUID
    ) -> None:
        task = asyncio.create_task(self._run(tenant_id, document_id, version_id))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _run(
        self, tenant_id: uuid.UUID, document_id: uuid.UUID, version_id: uuid.UUID
    ) -> None:
        try:
            result = await ingest(self._deps, tenant_id, document_id, version_id)
        except PermanentIngestionError:
            return  # recorded on the document
        except Exception:
            log.exception("inline_ingestion_failed", document_id=str(document_id))
            return
        if result.workflow_runs and self.start_runs is not None:
            await self.start_runs(tenant_id, result.workflow_runs)

    async def drain(self) -> None:
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
