"""Re-index documents from their stored originals, e.g. after changing the embedding model,
the chunker or the sensitivity rules (ARCHITECTURE section 8). Runs inline, one at a time."""

import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy import select

from needwise.core.tenancy import tenant_transaction
from needwise.modules.knowledge.models import Document
from needwise.modules.knowledge.pipeline import IngestionDeps, ingest

log = structlog.get_logger("needwise.reindex")


@dataclass
class ReindexResult:
    documents: int = 0
    chunks: int = 0
    failed: int = 0
    relabelled: int = 0


async def reindex_tenant(deps: IngestionDeps, tenant_id: uuid.UUID) -> ReindexResult:
    async with tenant_transaction(deps.sessions, tenant_id) as session:
        rows = (
            await session.execute(
                select(Document.id, Document.current_version_id, Document.sensitivity).where(
                    Document.current_version_id.is_not(None)
                )
            )
        ).all()
    result = ReindexResult()
    for document_id, version_id, before in rows:
        assert version_id is not None  # noqa: S101  (filtered above)
        try:
            indexed = await ingest(deps, tenant_id, document_id, version_id)
        except Exception as exc:
            log.warning("reindex_failed", document_id=str(document_id), error=str(exc)[:200])
            result.failed += 1
            continue
        result.documents += 1
        result.chunks += indexed.chunks
        result.relabelled += indexed.sensitivity != before
    return result
