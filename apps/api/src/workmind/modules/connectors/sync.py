"""Incremental sync of one source (ARCHITECTURE section 10.3).

    scan -> [Enterprise: the source's ACLs, written onto the documents synced before]
         -> per item: unchanged (skip) | new or changed (fetch, new version, ingest)
         -> items the source reported deleted (delta tombstones) are deleted everywhere
         -> if the scan was complete: items that disappeared are deleted everywhere
            if it was cut short: they are kept and flagged "missing upstream"
            either way, documents under a legal hold stay (and the attempt is audited); a
            held document the source deleted is marked (`source_deleted_at`) and every later
            sync tries again, so it goes with the first sync after its last hold is released
            (a delta feed reports a deletion only once)
         -> health recorded on the source and as a run (the run history); a source that keeps
            failing alerts the people who manage it (reminders.py)

Safe by construction:
- one sync per source at a time (a conditional status update acts as the lock);
- a failed scan deletes nothing; a failed item is counted and retried next time: when a
  download fails, the source's cursor does not advance, so an incremental source (a delta
  feed) reports the item again;
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
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from workmind.core.context import RequestContext
from workmind.core.errors import ApiError
from workmind.core.extensions import ExtensionRegistry
from workmind.core.tenancy import tenant_transaction
from workmind.modules.connectors.base import Connector, ConnectorError, RemoteItem, ScanResult
from workmind.modules.connectors.extensions import PERMISSION_SYNC, ItemAcl
from workmind.modules.connectors.models import Source, SourceRun
from workmind.modules.connectors.reminders import announce_recovery, check_failing
from workmind.modules.connectors.secrets import SecretBox
from workmind.modules.governance import holds
from workmind.modules.governance.audit import Actor, record_event
from workmind.modules.knowledge import document_acl, service
from workmind.modules.knowledge.dispatch import Dispatcher
from workmind.modules.knowledge.models import Collection, Document, DocumentGrant
from workmind.modules.knowledge.pipeline import delete_document_points
from workmind.modules.knowledge.storage import ObjectStorage, owned_key
from workmind.modules.retrieval.qdrant import QdrantClient

log = structlog.get_logger("workmind.sync")
STALE_LOCK = timedelta(hours=1)  # a "syncing" status older than this belongs to a dead worker
RUNS_KEPT = 100  # run history per source
INTERRUPTED = "the worker stopped before the sync finished"
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
    # Failed syncs in a row before the people who manage the source are alerted.
    failure_alert_after: int = 3
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
    # Gone from the source but kept: a legal hold covers them (docs/enterprise.md#legal-hold).
    kept_under_hold: int = 0
    failed: int = 0
    missing: int = 0  # known items a cut-short scan did not list (kept, flagged)
    complete: bool | None = None
    # Permission sync: documents whose own access list changed, and entries the source named
    # that map to nobody in WorkMind (they grant nothing; Admin lists them).
    acl_changed: int = 0
    acl_unmapped: int = 0
    errors: list[str] = field(default_factory=list[str])
    # Per-item errors keep the item's id apart from the message (the run history shows both).
    item_errors: list[tuple[str | None, str]] = field(default_factory=list[tuple[str | None, str]])

    def stats(self) -> dict[str, Any]:
        skip = ("status", "errors", "item_errors", "complete")
        return {k: v for k, v in self.__dict__.items() if k not in skip}

    def error(self, message: str, item: str | None = None) -> None:
        self.errors.append((f"{item}: {message}" if item else message)[:300])
        self.item_errors.append((item, message[:300]))


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


async def _claim(
    deps: SyncDeps,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID,
    trigger: str,
    requested_by: uuid.UUID | None,
) -> tuple[Source, uuid.UUID] | None:
    """Marks the source as syncing unless another sync holds it, and opens its run. None: skip
    this run. Taking over from a dead worker closes that worker's run as interrupted."""
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
        await session.execute(
            update(SourceRun)
            .where(SourceRun.source_id == source_id, SourceRun.status == "running")
            .values(
                status="error",
                finished_at=now,
                errors=[{"item": None, "message": INTERRUPTED}],
            )
        )
        run = SourceRun(
            tenant_id=tenant_id,
            source_id=source_id,
            trigger=trigger,
            requested_by=requested_by,
            status="running",
            started_at=now,
        )
        session.add(run)
        await session.flush()
        source = await session.get(Source, source_id)
        assert source is not None  # noqa: S101  (just claimed)
        return source, run.id


async def sync_source(
    deps: SyncDeps,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID,
    *,
    trigger: str = "schedule",
    requested_by: uuid.UUID | None = None,
) -> SyncReport:
    claim = await _claim(deps, tenant_id, source_id, trigger, requested_by)
    if claim is None:
        return SyncReport(status="skipped")
    source, run_id = claim
    report = SyncReport()
    cursor: dict[str, Any] = dict(source.cursor)
    connector: Connector | None = None
    try:
        credentials = deps.secrets.open(source.id, source.credentials) if source.credentials else {}
        connector = deps.connectors(source, credentials)
        scan = await connector.scan(source.cursor)
        report.items = len(scan.items)
        report.complete = scan.complete
        acls = await _source_acls(deps, source, connector, scan, report)
        if acls is not None:
            # Existing documents first: a narrowed list applies before any new content.
            changed = await apply_acls(
                deps.sessions, deps.qdrant, deps.collection_name, source, acls
            )
            report.acl_changed += changed
        retry = await _apply(deps, source, connector, scan.items, report, acls)
        # A download that failed is retried from the same position, not skipped for good.
        cursor = dict(source.cursor) if retry else scan.cursor
        present = {i.external_id for i in scan.items}
        if scan.deleted:
            await _delete(deps, source, set(scan.deleted) - present, report)
        if scan.complete:
            await _delete(deps, source, None, report, keep=present)
        else:  # deleted upstream earlier and kept by a hold: gone once no hold covers them
            pending = await _deleted_upstream(deps, source) - present - set(scan.deleted)
            if pending:
                await _delete(deps, source, pending, report)
        await _mark_seen(deps, source, present, flag_missing=scan.truncated, report=report)
        if acls is not None:
            report.acl_unmapped = await unmapped_entries(deps.sessions, source)
        report.status = (
            "error"
            if report.failed and not (report.added or report.updated or report.unchanged)
            else "ok"
        )
    except ConnectorError as exc:
        report.status = "error"
        report.error(str(exc))
    except Exception as exc:
        log.exception("sync_failed", source=str(source_id))
        report.status = "error"
        report.error(f"{type(exc).__name__}: {exc}")
    # A renewed OAuth token, kept even when the sync failed afterwards: Atlassian and Microsoft
    # rotate refresh tokens, so the old one may already be spent.
    refreshed = getattr(connector, "updated_credentials", None) if connector else None
    if isinstance(refreshed, dict):
        await _store_credentials(deps, source, refreshed)  # pyright: ignore[reportUnknownArgumentType]
    await _finish(deps, source, run_id, report, cursor)
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
            report.error(f"permissions not read, nobody sees it: {acl.error}", external_id)
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
) -> bool:
    """Stores new and changed items. True when a download failed (worth retrying), as
    opposed to an item that cannot be stored as it is (too large, an unreadable type)."""
    ctx = _owner_context(source)
    retry = False
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
            try:
                data = await connector.fetch(item)
            except ConnectorError:
                retry = True
                raise
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
                    synced=service.SyncedItem(
                        source.id, item.external_id, item.title, item.url, dict(item.meta)
                    ),
                    acl=entries,
                    acl_origin="source",
                )
                document = await session.get(Document, result.document.id)
                assert document is not None  # noqa: S101
                document.source_version = item.version
                if not result.unchanged:  # new content: a new staleness clock
                    document.source_changed_at = item.modified_at or datetime.now(UTC)
                    document.stale_reminded_at = None
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
            report.error(str(exc), item.external_id)
    for document_id, version_id in to_ingest:
        await deps.ingestion.dispatch(source.tenant_id, document_id, version_id)
    return retry


async def _delete(
    deps: SyncDeps,
    source: Source,
    ids: set[str] | None,
    report: SyncReport,
    *,
    keep: set[str] | None = None,
) -> None:
    """Deletion propagation: gone from the source means gone from search. Deletes the
    source's documents with these external ids, or (ids=None) every one not in `keep`. A
    document under a legal hold stays, searchable as before; each sync that would have deleted
    it records one audit event listing what it kept."""
    if ids is not None and not ids:
        return
    ctx = _owner_context(source)
    query = select(Document).where(Document.source_id == source.id)
    if ids is not None:
        query = query.where(Document.external_id.in_(sorted(ids)))
    async with tenant_transaction(deps.sessions, source.tenant_id) as session:
        missing = [
            d
            for d in await session.scalars(query)
            if ids is not None or d.external_id not in (keep or set())
        ]
        gone: list[Document] = []
        kept: dict[str, list[str]] = {}
        matters: set[str] = set()
        now = datetime.now(UTC)
        for document in missing:
            if blocking := await holds.covering(session, "document", document):
                kept[str(document.id)] = [str(h.id) for h in blocking]
                matters.update(h.matter for h in blocking)
                if document.source_deleted_at is None:  # deleted upstream: retried every sync
                    document.source_deleted_at = now
            else:
                gone.append(document)
        orphaned: list[str] = []
        for document in gone:
            orphaned += await service.delete_document(session, ctx, document)
        if kept:
            await record_event(
                session,
                tenant_id=source.tenant_id,
                actor=Actor(type="service", id=source.owner_id),
                action=holds.BLOCKED,
                resource_type="source",
                resource_id=str(source.id),
                details={
                    "via": "connector",
                    "source": source.key,
                    "documents": kept,
                    "matters": sorted(matters),
                },
            )
    for document in gone:
        await delete_document_points(
            deps.qdrant, deps.collection_name, source.tenant_id, document.id
        )
    for key in set(orphaned):
        await deps.storage.delete(owned_key(source.tenant_id, key))
    report.deleted += len(gone)
    report.kept_under_hold += len(kept)


async def _deleted_upstream(deps: SyncDeps, source: Source) -> set[str]:
    """External ids of this source's documents the source deleted while a hold kept them."""
    async with tenant_transaction(deps.sessions, source.tenant_id) as session:
        return {
            external_id
            for external_id in await session.scalars(
                select(Document.external_id).where(
                    Document.source_id == source.id, Document.source_deleted_at.is_not(None)
                )
            )
            if external_id is not None
        }


async def _mark_seen(
    deps: SyncDeps, source: Source, present: set[str], *, flag_missing: bool, report: SyncReport
) -> None:
    """Items the scan listed are not missing; after a scan that was cut short, known items it
    did not list are flagged "missing upstream" (since the first such scan) and kept."""
    now = datetime.now(UTC)
    async with tenant_transaction(deps.sessions, source.tenant_id) as session:
        documents = (
            await session.scalars(select(Document).where(Document.source_id == source.id))
        ).all()
        for document in documents:
            if document.external_id in present:
                if document.source_missing_since is not None:
                    document.source_missing_since = None
                if document.source_deleted_at is not None:  # back in the source
                    document.source_deleted_at = None
                if document.source_changed_at is None:  # synced before this was recorded
                    document.source_changed_at = now
            elif flag_missing and document.source_missing_since is None:
                document.source_missing_since = now
        report.missing = sum(1 for d in documents if d.source_missing_since is not None)


async def _store_credentials(deps: SyncDeps, source: Source, credentials: dict[str, Any]) -> None:
    async with tenant_transaction(deps.sessions, source.tenant_id) as session:
        stored = await session.get(Source, source.id)
        if stored is not None:
            stored.credentials = deps.secrets.seal(source.id, credentials)


async def _finish(
    deps: SyncDeps,
    source: Source,
    run_id: uuid.UUID,
    report: SyncReport,
    cursor: dict[str, Any],
) -> None:
    now = datetime.now(UTC)
    async with tenant_transaction(deps.sessions, source.tenant_id) as session:
        run = await session.get(SourceRun, run_id)
        if run is not None:
            run.status = report.status
            run.finished_at = now
            run.complete = report.complete
            run.stats = report.stats()
            run.errors = [{"item": item, "message": m} for item, m in report.item_errors[:50]]
        # Locked: the daily reminder task reads the failure episode too.
        stored = await session.scalar(
            select(Source).where(Source.id == source.id).with_for_update()
        )
        if stored is None:
            return
        stored.status = report.status
        stored.cursor = cursor
        stored.stats = report.stats()
        stored.last_error = "; ".join(report.errors[:5]) or None
        if report.status == "ok":
            stored.last_success_at = now
            stored.consecutive_failures = 0
            if stored.failure_alerted_at is not None:
                await announce_recovery(session, stored)  # names the episode it ends
                stored.failure_alerted_at = None
        else:
            stored.consecutive_failures += 1
            await check_failing(session, stored, threshold=deps.failure_alert_after, now=now)
        await record_event(
            session,
            tenant_id=source.tenant_id,
            actor=Actor.system(),
            action="source.synced",
            resource_type="source",
            resource_id=source.key,
            details={"status": report.status, **report.stats()},
        )
        # The run history keeps the newest RUNS_KEPT runs of each source.
        keep = (
            select(SourceRun.id)
            .where(SourceRun.source_id == source.id)
            .order_by(SourceRun.started_at.desc())
            .limit(RUNS_KEPT)
        )
        await session.execute(
            delete(SourceRun)
            .where(SourceRun.source_id == source.id, SourceRun.id.not_in(keep))
            .execution_options(synchronize_session=False)
        )
    log.info("source_synced", source=source.key, status=report.status, **report.stats())
