"""The ingestion pipeline (ARCHITECTURE section 10): one document version from stored bytes to
searchable chunks.

    stored file -> parse -> chunk -> sensitivity scan -> embed (dense + BM25) -> Qdrant + Postgres
    -> "document.ready": workflows listening for it get a queued run (in the same transaction)

New points are written before the previous version's points are removed, so a document is never
missing from search while it is re-indexed. Every chunk carries the collection's ACL (lock 1).
"""

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from workmind.core.tenancy import tenant_transaction
from workmind.modules.governance.audit import Actor, record_event
from workmind.modules.knowledge.acl import acl_principals
from workmind.modules.knowledge.chunking import chunk_document, embedding_text
from workmind.modules.knowledge.models import (
    Chunk,
    Collection,
    CollectionGrant,
    Document,
    DocumentVersion,
)
from workmind.modules.knowledge.parsing import UnsupportedFormatError, parse
from workmind.modules.knowledge.sensitivity import scan, suggest
from workmind.modules.knowledge.storage import ObjectStorage
from workmind.modules.retrieval.qdrant import QdrantClient, match
from workmind.modules.retrieval.sparse import encode_document
from workmind.modules.work.events import DocumentReady, document_ready
from workmind.providers.embeddings import EmbeddingProvider, EmbeddingRejectedError

log = structlog.get_logger("workmind.ingestion")


class PermanentIngestionError(RuntimeError):
    """Retrying cannot help (unreadable file, no text). Transient errors propagate as-is."""


@dataclass(frozen=True)
class IngestionDeps:
    sessions: async_sessionmaker[AsyncSession]
    storage: ObjectStorage
    embeddings: EmbeddingProvider
    qdrant: QdrantClient
    collection_name: str


@dataclass(frozen=True)
class IngestionResult:
    document_id: uuid.UUID
    chunks: int
    sensitivity: str
    # Runs of workflows triggered by this version (queued: the caller starts them).
    workflow_runs: tuple[uuid.UUID, ...] = ()


async def ingest(
    deps: IngestionDeps, tenant_id: uuid.UUID, document_id: uuid.UUID, version_id: uuid.UUID
) -> IngestionResult:
    try:
        return await _ingest(deps, tenant_id, document_id, version_id)
    except PermanentIngestionError as exc:
        await _mark_failed(deps, tenant_id, document_id, str(exc))
        raise
    except Exception as exc:
        await _mark_failed(deps, tenant_id, document_id, f"{type(exc).__name__}: {exc}"[:500])
        raise


async def _ingest(
    deps: IngestionDeps, tenant_id: uuid.UUID, document_id: uuid.UUID, version_id: uuid.UUID
) -> IngestionResult:
    async with tenant_transaction(deps.sessions, tenant_id) as session:
        document = await session.get(Document, document_id)
        version = await session.get(DocumentVersion, version_id)
        if document is None or version is None:
            raise PermanentIngestionError("document or version no longer exists")
        collection = await session.get(Collection, document.collection_id)
        assert collection is not None  # noqa: S101  (enforced by a foreign key)
        grants = (
            await session.scalars(
                select(CollectionGrant).where(CollectionGrant.collection_id == collection.id)
            )
        ).all()
        document.status = "processing"
        acl = acl_principals(collection, grants)
        default_sensitivity, filename, mime = (
            collection.default_sensitivity,
            document.filename,
            document.mime_type,
        )
        manual_sensitivity = document.sensitivity_manual
        # A synced item keeps the name its source gives it (a page title, a Drive file name).
        source_title = document.title if document.source_id is not None else None
        trust, sensitivity = document.trust, document.sensitivity
        storage_key = version.storage_key

    data = await deps.storage.get(storage_key)
    try:
        parsed = await asyncio.to_thread(parse, data, filename, mime)
    except (UnsupportedFormatError, ValueError, KeyError, OSError) as exc:
        raise PermanentIngestionError(f"could not read the file: {exc}") from exc
    drafts = chunk_document(parsed)
    if not drafts:
        raise PermanentIngestionError("no readable text found (a scanned PDF needs OCR)")

    title = source_title or parsed.title
    findings = scan("\n".join(d.text for d in drafts))
    if not manual_sensitivity:
        sensitivity = suggest(default_sensitivity, findings)

    inputs = [embedding_text(d.heading_path, d.text) for d in drafts]
    try:
        dense = await deps.embeddings.embed_documents(inputs, titles=[title] * len(inputs))
    except EmbeddingRejectedError as exc:
        raise PermanentIngestionError(f"the embedding model rejected this document: {exc}") from exc
    chunk_ids = [uuid.uuid4() for _ in drafts]
    now = datetime.now(UTC).isoformat()
    points: list[dict[str, Any]] = [
        {
            "id": str(chunk_id),
            "vector": {"dense": vector, "bm25": encode_document(text).as_qdrant()},
            "payload": {
                "tenant_id": str(tenant_id),
                "collection_id": str(collection.id),
                "document_id": str(document_id),
                "version_id": str(version_id),
                "chunk_id": str(chunk_id),
                "ordinal": draft.ordinal,
                "acl": acl,
                "trust": trust,
                "sensitivity": sensitivity,
                "title": title,
                "filename": filename,
                "heading_path": list(draft.heading_path),
                "page": draft.page,
                "text": draft.text,
                "is_active": True,
                "indexed_at": now,
            },
        }
        for chunk_id, draft, vector, text in zip(chunk_ids, drafts, dense, inputs, strict=True)
    ]
    await deps.qdrant.ensure_collection(deps.collection_name, dense_size=deps.embeddings.dimensions)
    await deps.qdrant.upsert(deps.collection_name, points, tenant_id=tenant_id)

    async with tenant_transaction(deps.sessions, tenant_id) as session:
        document = await session.get(Document, document_id)
        version = await session.get(DocumentVersion, version_id)
        if document is None or version is None:  # deleted while we were embedding
            await deps.qdrant.delete(
                deps.collection_name,
                {"must": [match("version_id", str(version_id))]},
                tenant_id=tenant_id,
            )
            raise PermanentIngestionError("document was deleted during ingestion")
        await session.execute(delete(Chunk).where(Chunk.document_id == document_id))
        session.add_all(
            Chunk(
                id=chunk_id,
                tenant_id=tenant_id,
                document_id=document_id,
                version_id=version_id,
                ordinal=draft.ordinal,
                text=draft.text,
                heading_path=list(draft.heading_path),
                page=draft.page,
                token_count=draft.token_count,
            )
            for chunk_id, draft in zip(chunk_ids, drafts, strict=True)
        )
        version.chunk_count, version.processed_at = len(drafts), datetime.now(UTC)
        version.embedding_model = deps.embeddings.model_id
        document.current_version_id, document.status, document.error = version_id, "ready", None
        document.title, document.sensitivity = title, sensitivity
        await record_event(
            session,
            tenant_id=tenant_id,
            actor=Actor.system(),
            action="knowledge.document.indexed",
            resource_type="document",
            resource_id=str(document_id),
            details={
                "chunks": len(drafts),
                "sensitivity": sensitivity,
                "signals": sorted(findings),
            },
        )
        workflow_runs = await document_ready(
            session,
            tenant_id,
            DocumentReady(
                document_id=document_id,
                version_id=version_id,
                version=version.version_no,
                collection_id=document.collection_id,
                title=title,
                filename=filename,
                source=document.source,
                url=document.url,
            ),
        )

    # Only now remove every older point of this document, from earlier versions or from an
    # earlier run of this same version (re-index): search never sees a gap or a duplicate.
    await deps.qdrant.delete(
        deps.collection_name,
        {
            "must": [match("document_id", str(document_id))],
            "must_not": [{"has_id": [str(chunk_id) for chunk_id in chunk_ids]}],
        },
        tenant_id=tenant_id,
    )
    log.info(
        "document_indexed",
        document_id=str(document_id),
        chunks=len(drafts),
        sensitivity=sensitivity,
    )
    return IngestionResult(
        document_id=document_id,
        chunks=len(drafts),
        sensitivity=sensitivity,
        workflow_runs=tuple(workflow_runs),
    )


async def _mark_failed(
    deps: IngestionDeps, tenant_id: uuid.UUID, document_id: uuid.UUID, error: str
) -> None:
    async with tenant_transaction(deps.sessions, tenant_id) as session:
        document = await session.get(Document, document_id)
        if document is not None:
            document.status, document.error = "failed", error
    log.warning("document_failed", document_id=str(document_id), error=error)


async def refresh_collection_acl(
    qdrant: QdrantClient,
    collection_name: str,
    tenant_id: uuid.UUID,
    collection: Collection,
    grants: list[CollectionGrant],
) -> None:
    """After a sharing change: rewrite the ACL on every chunk of the collection (lock 1).
    Usersets, not expanded users, are stored, so membership changes never need this."""
    if not await qdrant.collection_exists(collection_name):
        return
    await qdrant.set_payload(
        collection_name,
        {"acl": acl_principals(collection, grants)},
        {"must": [match("collection_id", str(collection.id))]},
        tenant_id=tenant_id,
    )


async def set_document_labels(
    qdrant: QdrantClient,
    collection_name: str,
    tenant_id: uuid.UUID,
    document_id: uuid.UUID,
    labels: dict[str, str],
) -> None:
    """After a trust or sensitivity change: every chunk of the document carries the new labels,
    so ranking and model routing follow at once."""
    if not await qdrant.collection_exists(collection_name):
        return
    await qdrant.set_payload(
        collection_name,
        labels,
        {"must": [match("document_id", str(document_id))]},
        tenant_id=tenant_id,
    )


async def delete_document_points(
    qdrant: QdrantClient, collection_name: str, tenant_id: uuid.UUID, document_id: uuid.UUID
) -> None:
    if await qdrant.collection_exists(collection_name):
        await qdrant.delete(
            collection_name,
            {"must": [match("document_id", str(document_id))]},
            tenant_id=tenant_id,
        )
