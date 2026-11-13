"""Incremental sync of one source (ARCHITECTURE section 10.3).

    scan -> [Enterprise: the source's ACLs, written onto the documents synced before]
         -> per item: unchanged (skip) | new or changed (fetch, new version, ingest)
         -> if the scan was complete: items that disappeared are deleted everywhere
         -> health recorded on the source

Safe by construction:
- one sync per source at a time (a conditional status update acts as the lock);
- a failed scan deletes nothing; a failed item is counted and retried next time;
- every synced document lands in the source's collection, with that collection's access;
- with permission sync (Enterprise, docs/permission-sync.md), each document also gets the
  source's ACL as its own access list, which only narrows the collection's. ACLs are read
  before any content is applied, a new document gets its list in the transaction that creates
  it (never indexed or visible with the wider access first), and a failure to read them fails
  the sync without changing anything. Without the license nothing about ACLs happens: lists
  already written stay as they are (docs/permission-sync.md says what that means).
"""

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from workmind.core.context import RequestContext
from workmind.core.errors import ApiError
from workmind.core.extensions import ExtensionRegistry
from workmind.core.tenancy import tenant_transaction
from workmind.modules.connectors.base import Connector, ConnectorError, RemoteItem, ScanResult
from workmind.modules.connectors.extensions import PERMISSION_SYNC, ItemAcl
from workmind.modules.connectors.models import Source
from workmind.modules.connectors.secrets import SecretBox
from workmind.modules.governance.audit import Actor, record_event
from workmind.modules.knowledge import document_acl, service
from workmind.modules.knowledge.dispatch import Dispatcher
from workmind.modules.knowledge.models import Collection, Document, DocumentGrant
from workmind.modules.knowledge.pipeline import delete_document_points
from workmind.modules.knowledge.storage import ObjectStorage, owned_key
from workmind.modules.retrieval.qdrant import QdrantClient

log = structlog.get_logger("workmind.sync")
STALE_LOCK = timedelta(hours=1)  # a "syncing" status older than this belongs to a dead worker
# Chat is discussion, never policy (ARCHITECTURE 7.3), whatever the collection's default
# trust: anyone in a synced channel (Slack Connect guests included) writes it, and an
# official-by-default collection would otherwise rank their messages above real policy
# and let them win conflicts (data poisoning, docs/security/owasp-llm-top10.md LLM04).
FIXED_TRUST: dict[str, str] = {"slack": "discussion"}

ConnectorFactory = Callable[[Source, Mapping[str, Any]], Connector]


@dataclass
class SyncDeps:
    sessions: async_sessionmaker[AsyncSession]
    storage: ObjectStorage
    ingestion: Dispatcher
    qdrant: QdrantClient
    collection_name: str
    secrets: SecretBox
    connectors: ConnectorFactory
    max_bytes: int
    # The process's extensions: permission sync runs only when it is licensed and implemented.
    extensions: ExtensionRegistry | None = None


@dataclass
class SyncReport:
    status: str = "ok"
    items: int = 0
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    deleted: int = 0
    failed: int = 0
    # Permission sync: documents whose own access list changed, and entries the source named
    # that map to nobody in WorkMind (they grant nothing; Admin lists them).
    acl_changed: int = 0
    acl_unmapped: int = 0
    errors: list[str] = field(default_factory=list[str])

    def stats(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if k not in ("status", "errors")}


def _owner_context(source: Source) -> RequestContext:
    return RequestContext(
        request_id=f"sync-{uuid.uuid4().hex[:8]}",
        tenant_id=source.tenant_id,
        user_id=source.owner_id,
        org_role="member",
        principals=frozenset(),
        auth_method="service",
        scopes=frozenset({"*"}),
    )


async def _claim(deps: SyncDeps, tenant_id: uuid.UUID, source_id: uuid.UUID) -> Source | None:
    """Marks the source as syncing unless another sync holds it. None: skip this run."""
    now = datetime.now(UTC)
    async with tenant_transaction(deps.sessions, tenant_id) as session:
        claimed = await session.scalar(
            update(Source)
            .where(
                Source.id == source_id,
                Source.enabled.is_(True),
                (Source.status != "syncing") | (Source.last_sync_at < now - STALE_LOCK),
            )
            .values(status="syncing", last_sync_at=now)
            .returning(Source.id)
        )
        if claimed is None:
            return None
        return await session.get(Source, source_id)


async def sync_source(deps: SyncDeps, tenant_id: uuid.UUID, source_id: uuid.UUID) -> SyncReport:
    source = await _claim(deps, tenant_id, source_id)
    if source is None:
        return SyncReport(status="skipped")
    report = SyncReport()
    cursor: dict[str, Any] = dict(source.cursor)
    try:
        credentials = deps.secrets.open(source.id, source.credentials) if source.credentials else {}
        connector = deps.connectors(source, credentials)
        scan = await connector.scan(source.cursor)
        refreshed = getattr(connector, "updated_credentials", None)  # e.g. a new OAuth token
        if isinstance(refreshed, dict):
            await _store_credentials(deps, source, refreshed)  # pyright: ignore[reportUnknownArgumentType]
        report.items = len(scan.items)
        cursor = scan.cursor
        acls = await _source_acls(deps, source, connector, scan, report)
        if acls is not None:
            # Existing documents first: a narrowed list applies before any new content.
            changed = await apply_acls(
                deps.sessions, deps.qdrant, deps.collection_name, source, acls
            )
            report.acl_changed += changed
        await _apply(deps, source, connector, scan.items, report, acls)
        if scan.complete:
            await _delete_missing(deps, source, {i.external_id for i in scan.items}, report)
        if acls is not None:
            report.acl_unmapped = await unmapped_entries(deps.sessions, source)
        report.status = (
            "error"
            if report.failed and not (report.added or report.updated or report.unchanged)
            else "ok"
        )
    except ConnectorError as exc:
        report.status = "error"
        report.errors.append(str(exc)[:300])
    except Exception as exc:
        log.exception("sync_failed", source=str(source_id))
        report.status = "error"
        report.errors.append(f"{type(exc).__name__}: {exc}"[:300])
    await _finish(deps, source, report, cursor)
    return report


async def _source_acls(
    deps: SyncDeps, source: Source, connector: Connector, scan: ScanResult, report: SyncReport
) -> dict[str, ItemAcl] | None:
    """The source's ACL for every scanned item (and any synced item it also covers), or None
    when permission sync is not licensed or not available for this kind of source."""
    if deps.extensions is None:
        return None
    syncers = [s for s in deps.extensions.call(PERMISSION_SYNC) if s.supports(source)]
    if not syncers:
        return None
    async with tenant_transaction(deps.sessions, source.tenant_id) as session:
        known = set(
            await session.scalars(
                select(Document.external_id).where(
                    Document.source_id == source.id, Document.external_id.is_not(None)
                )
            )
        )
    found = dict(await syncers[0].acls(source, connector, scan.items, {k for k in known if k}))
    for item in scan.items:
        found.setdefault(item.external_id, ItemAcl())  # left out: nobody (fail closed)
    for external_id, acl in sorted(found.items()):
        if acl.error:
            report.errors.append(
                f"{external_id}: permissions not read, nobody sees it: {acl.error}"[:300]
            )
    return found


async def apply_acls(
    sessions: async_sessionmaker[AsyncSession],
    qdrant: QdrantClient,
    collection_name: str,
    source: Source,
    acls: Mapping[str, ItemAcl],
) -> int:
    """Writes the source's ACLs onto the documents it already synced (identity mapping: fail
    closed), queues the OpenFGA changes in the same transaction, and once committed refreshes
    the chunks of every document whose list changed. Used by every sync and by Enterprise's
    immediate revocation (Drive changes). Returns how many documents changed."""
    payloads: dict[uuid.UUID, dict[str, Any]] = {}
    async with tenant_transaction(sessions, source.tenant_id) as session:
        documents = (
            await session.scalars(
                select(Document).where(
                    Document.source_id == source.id, Document.external_id.in_(sorted(acls))
                )
            )
        ).all()
        externals = {p for d in documents for p in acls[d.external_id or ""].principals}
        resolved = await document_acl.resolve(session, source.tenant_id, externals)
        for document in documents:
            entries = acl_entries(acls[document.external_id or ""], resolved)
            if await document_acl.set_acl(
                session,
                source.tenant_id,
                document,
                entries,
                origin="source",
                actor=Actor.system(),
            ):
                payloads[document.id] = await document_acl.payload_of(session, document)
    for document_id, payload in payloads.items():
        await document_acl.refresh_payload(
            qdrant, collection_name, source.tenant_id, document_id, payload
        )
    return len(payloads)


async def unmapped_entries(sessions: async_sessionmaker[AsyncSession], source: Source) -> int:
    """Entries on the source's documents that map to nobody (they grant nothing)."""
    async with tenant_transaction(sessions, source.tenant_id) as session:
        count = await session.scalar(
            select(func.count())
            .select_from(DocumentGrant)
            .join(Document, Document.id == DocumentGrant.document_id)
            .where(Document.source_id == source.id, DocumentGrant.principal.is_(None))
        )
    return count or 0


def acl_entries(
    acl: ItemAcl, resolved: Mapping[str, str | None]
) -> list[document_acl.AclEntry] | None:
    """An own list from what the source says (None: the collection's access, no list)."""
    if not acl.restricted:
        return None
    names = {document_acl.normalize_external(p) for p in acl.principals}
    return document_acl.ordered(document_acl.AclEntry(resolved.get(n), n) for n in names)


async def _apply(
    deps: SyncDeps,
    source: Source,
    connector: Connector,
    items: list[RemoteItem],
    report: SyncReport,
    acls: Mapping[str, ItemAcl] | None = None,
) -> None:
    ctx = _owner_context(source)
    async with tenant_transaction(deps.sessions, source.tenant_id) as session:
        known = {
            d.external_id: d
            for d in await session.scalars(select(Document).where(Document.source_id == source.id))
        }
        collection = await session.get(Collection, source.collection_id)
    if collection is None:
        raise ConnectorError("the source's collection no longer exists")
    to_ingest: list[tuple[uuid.UUID, uuid.UUID]] = []
    for item in items:
        existing = known.get(item.external_id)
        if existing and existing.source_version == item.version and existing.status != "failed":
            report.unchanged += 1
            continue
        try:
            data = await connector.fetch(item)
            if len(data) > deps.max_bytes:
                raise ConnectorError(f"{item.title}: larger than {deps.max_bytes // 1_000_000} MB")
            async with tenant_transaction(deps.sessions, source.tenant_id) as session:
                entries: list[document_acl.AclEntry] | None = None
                if acls is not None:
                    acl = acls.get(item.external_id, ItemAcl())
                    resolved = await document_acl.resolve(session, source.tenant_id, acl.principals)
                    entries = acl_entries(acl, resolved)
                result = await service.upload(
                    session,
                    ctx,
                    deps.storage,
                    collection,
                    filename=item.filename,
                    declared_mime=None,
                    data=data,
                    trust=FIXED_TRUST.get(source.kind),
                    source=source.kind,
                    synced=service.SyncedItem(source.id, item.external_id, item.title, item.url),
                    acl=entries,
                    acl_origin="source",
                )
                document = await session.get(Document, result.document.id)
                assert document is not None  # noqa: S101
                document.source_version = item.version
            if not result.unchanged:
                to_ingest.append((result.document.id, result.version.id))
            if existing is None:
                report.added += 1
            elif result.unchanged:
                report.unchanged += 1
            else:
                report.updated += 1
        except (ConnectorError, ApiError) as exc:
            report.failed += 1
            report.errors.append(f"{item.external_id}: {exc}"[:300])
    for document_id, version_id in to_ingest:
        await deps.ingestion.dispatch(source.tenant_id, document_id, version_id)


async def _delete_missing(
    deps: SyncDeps, source: Source, present: set[str], report: SyncReport
) -> None:
    """Deletion propagation: gone from the source means gone from search."""
    ctx = _owner_context(source)
    async with tenant_transaction(deps.sessions, source.tenant_id) as session:
        gone = [
            d
            for d in await session.scalars(select(Document).where(Document.source_id == source.id))
            if d.external_id not in present
        ]
        orphaned: list[str] = []
        for document in gone:
            orphaned += await service.delete_document(session, ctx, document)
    for document in gone:
        await delete_document_points(
            deps.qdrant, deps.collection_name, source.tenant_id, document.id
        )
    for key in set(orphaned):
        await deps.storage.delete(owned_key(source.tenant_id, key))
    report.deleted = len(gone)


async def _store_credentials(deps: SyncDeps, source: Source, credentials: dict[str, Any]) -> None:
    async with tenant_transaction(deps.sessions, source.tenant_id) as session:
        stored = await session.get(Source, source.id)
        if stored is not None:
            stored.credentials = deps.secrets.seal(source.id, credentials)


async def _finish(
    deps: SyncDeps, source: Source, report: SyncReport, cursor: dict[str, Any]
) -> None:
    now = datetime.now(UTC)
    async with tenant_transaction(deps.sessions, source.tenant_id) as session:
        stored = await session.get(Source, source.id)
        if stored is None:
            return
        stored.status = report.status
        stored.cursor = cursor
        stored.stats = report.stats()
        stored.last_error = "; ".join(report.errors[:5]) or None
        if report.status == "ok":
            stored.last_success_at = now
        await record_event(
            session,
            tenant_id=source.tenant_id,
            actor=Actor.system(),
            action="source.synced",
            resource_type="source",
            resource_id=source.key,
            details={"status": report.status, **report.stats()},
        )
    log.info("source_synced", source=source.key, status=report.status, **report.stats())
