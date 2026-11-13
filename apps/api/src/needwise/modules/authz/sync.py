"""Keeping OpenFGA in step with PostgreSQL (ARCHITECTURE section 7.5).

- `ensure_store`: one store per tenant, with the current model.
- `enqueue` / `process_outbox`: incremental changes, committed with the data that caused them.
- `reconcile`: recomputes every identity tuple from PostgreSQL and repairs any drift.
- `sync_tenants`: both of the above for every tenant (`nw authz sync`): after an upgrade that
  changes the model, and after a restore (docs/operations/backup-restore.md).

Both outbox processing and reconciliation hold a per-tenant advisory lock, so a reconcile
can never delete a tuple that a concurrently committed change just added.
"""

import asyncio
import hashlib
import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from needwise.core.tenancy import global_transaction, tenant_transaction
from needwise.modules.authz.models import AuthzOutbox
from needwise.modules.authz.openfga import OpenFgaClient, RelationTuple, authorization_model
from needwise.modules.identity.models import (
    Department,
    DepartmentMember,
    Group,
    GroupMember,
    Tenant,
    User,
)
from needwise.modules.knowledge.acl import collection_tuples, document_tuples
from needwise.modules.knowledge.models import (
    Collection,
    CollectionGrant,
    Document,
    DocumentGrant,
)

SessionFactory = async_sessionmaker[AsyncSession]
log = structlog.get_logger("needwise.authz")

# Object types NeedWise derives from PostgreSQL (identity and knowledge tables).
IDENTITY_TYPES = frozenset({"organization", "department", "group", "collection", "document"})
ORG_RELATION = {"owner": "owner", "admin": "admin", "member": "member", "auditor": "auditor"}
MAX_ATTEMPTS = 10


@dataclass(frozen=True)
class ReconcileResult:
    written: int
    deleted: int


async def ensure_store(
    owner_sessions: SessionFactory, fga: OpenFgaClient, tenant_id: uuid.UUID
) -> str:
    """Create the tenant's store if needed and make sure it runs the current model.
    Uses the owner role: only the owner may update the tenants table."""
    model = authorization_model()
    model_hash = hashlib.sha256(json.dumps(model, sort_keys=True).encode()).hexdigest()
    async with global_transaction(owner_sessions) as session:
        tenant = await session.get(Tenant, tenant_id, with_for_update=True)
        if tenant is None:
            raise ValueError(f"unknown tenant {tenant_id}")
        if tenant.openfga_store_id is None:
            tenant.openfga_store_id = await fga.create_store(f"tenant-{tenant.slug}")
        if tenant.settings.get("openfga_model_sha256") != model_hash:
            await fga.write_model(tenant.openfga_store_id, model)
            tenant.settings = {**tenant.settings, "openfga_model_sha256": model_hash}
        return tenant.openfga_store_id


def org_object(tenant_id: uuid.UUID) -> str:
    return f"organization:{tenant_id}"


async def desired_identity_tuples(
    session: AsyncSession, tenant_id: uuid.UUID
) -> set[RelationTuple]:
    """Every identity tuple the tenant should have, derived from PostgreSQL."""
    org = org_object(tenant_id)
    tuples: set[RelationTuple] = set()
    disabled: set[uuid.UUID] = set()
    for user in await session.scalars(select(User)):
        if user.status == "disabled":
            disabled.add(user.id)  # disabled users lose every relation
        elif relation := ORG_RELATION.get(user.org_role):  # guests get none
            tuples.add(RelationTuple(f"user:{user.id}", relation, org))
    for department_id in await session.scalars(select(Department.id)):
        tuples.add(RelationTuple(org, "organization", f"department:{department_id}"))
    for member in await session.scalars(select(DepartmentMember)):
        if member.user_id not in disabled:
            tuples.add(
                RelationTuple(
                    f"user:{member.user_id}", member.role, f"department:{member.department_id}"
                )
            )
    for group_id in await session.scalars(select(Group.id)):
        tuples.add(RelationTuple(org, "organization", f"group:{group_id}"))
    for member in await session.scalars(select(GroupMember)):
        group = f"group:{member.group_id}"
        if member.member_user_id and member.member_user_id not in disabled:
            tuples.add(RelationTuple(f"user:{member.member_user_id}", "member", group))
        elif member.member_group_id:
            tuples.add(RelationTuple(f"group:{member.member_group_id}#member", "member", group))
    grants = (await session.scalars(select(CollectionGrant))).all()
    for collection in await session.scalars(select(Collection)):
        own = [g for g in grants if g.collection_id == collection.id]
        tuples |= collection_tuples(tenant_id, collection, own)
    document_grants: dict[uuid.UUID, list[DocumentGrant]] = {}
    for grant in await session.scalars(select(DocumentGrant)):
        document_grants.setdefault(grant.document_id, []).append(grant)
    for document in await session.scalars(select(Document)):
        tuples |= document_tuples(document, document_grants.get(document.id, []))
    return tuples


async def _lock(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"authz:{tenant_id}"},
    )


async def reconcile(
    sessions: SessionFactory, fga: OpenFgaClient, tenant_id: uuid.UUID, store_id: str
) -> ReconcileResult:
    async with tenant_transaction(sessions, tenant_id) as session:
        await _lock(session, tenant_id)
        desired = await desired_identity_tuples(session, tenant_id)
        actual = {t for t in await fga.read_all(store_id) if t.object_type in IDENTITY_TYPES}
        writes, deletes = desired - actual, actual - desired
        await fga.write(store_id, writes=writes, deletes=deletes)
    if writes or deletes:
        log.info(
            "authz_reconciled", tenant_id=str(tenant_id), written=len(writes), deleted=len(deletes)
        )
    return ReconcileResult(written=len(writes), deleted=len(deletes))


@dataclass(frozen=True)
class TenantSync:
    slug: str
    store_id: str
    written: int
    deleted: int


async def sync_tenants(
    owner_sessions: SessionFactory, fga: OpenFgaClient, slug: str | None = None
) -> list[TenantSync]:
    """Every active tenant (or the one named): its store runs the current model, and its
    identity tuples match PostgreSQL, the source of truth. Needed after an upgrade that changes
    the model (only seeding writes it otherwise) and after a restore: the OpenFGA database is
    dumped apart from NeedWise's, so changes made between the two dumps differ. Safe to repeat;
    pending outbox rows stay and apply idempotently. Uses the owner role, like `ensure_store`."""
    async with global_transaction(owner_sessions) as session:
        query = select(Tenant.id, Tenant.slug).order_by(Tenant.slug)
        query = query.where(Tenant.slug == slug) if slug else query.where(Tenant.status == "active")
        tenants = list((await session.execute(query)).all())
    synced: list[TenantSync] = []
    for tenant_id, tenant_slug in tenants:
        store_id = await ensure_store(owner_sessions, fga, tenant_id)
        result = await reconcile(owner_sessions, fga, tenant_id, store_id)
        synced.append(TenantSync(tenant_slug, store_id, result.written, result.deleted))
    return synced


def enqueue(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    writes: Iterable[RelationTuple] = (),
    deletes: Iterable[RelationTuple] = (),
) -> None:
    """Record tuple changes in the caller's transaction (commit them with the data change)."""
    session.add(
        AuthzOutbox(
            tenant_id=tenant_id,
            writes=[t.key() for t in writes],
            deletes=[t.key() for t in deletes],
        )
    )


async def process_outbox(
    sessions: SessionFactory,
    fga: OpenFgaClient,
    tenant_id: uuid.UUID,
    store_id: str,
    batch: int = 50,
) -> int:
    """Apply pending changes in commit order. Stops at the first failure, so a later delete is
    never applied before an earlier write. Returns how many rows were applied."""
    applied = 0
    async with tenant_transaction(sessions, tenant_id) as session:
        await _lock(session, tenant_id)
        rows = (
            await session.scalars(
                select(AuthzOutbox)
                .where(AuthzOutbox.status == "pending")
                .order_by(AuthzOutbox.id)
                .limit(batch)
                .with_for_update(skip_locked=True)
            )
        ).all()
        for row in rows:
            try:
                await fga.write(
                    store_id,
                    writes=[RelationTuple.from_key(k) for k in row.writes],
                    deletes=[RelationTuple.from_key(k) for k in row.deletes],
                )
            except Exception as exc:
                row.attempts += 1
                row.last_error = str(exc)[:500]
                if row.attempts >= MAX_ATTEMPTS:
                    row.status = "failed"
                log.warning("authz_outbox_failed", outbox_id=str(row.id), error=row.last_error)
                break
            row.status, row.processed_at = "done", datetime.now(UTC)
            applied += 1
    return applied


async def tenants_with_stores(sessions: SessionFactory) -> list[tuple[uuid.UUID, str]]:
    async with global_transaction(sessions) as session:
        rows = await session.execute(
            select(Tenant.id, Tenant.openfga_store_id).where(Tenant.openfga_store_id.is_not(None))
        )
        return [(tenant_id, store_id) for tenant_id, store_id in rows if store_id]


class OutboxWorker:
    """Drains every tenant's outbox in the background. Phase 2 moves this to a Celery worker;
    row locks (SKIP LOCKED) already make several concurrent workers safe."""

    def __init__(
        self, sessions: SessionFactory, fga: OpenFgaClient, interval_s: float = 2.0
    ) -> None:
        self._sessions = sessions
        self._fga = fga
        self._interval = interval_s

    async def run_forever(self) -> None:
        while True:
            try:
                for tenant_id, store_id in await tenants_with_stores(self._sessions):
                    await process_outbox(self._sessions, self._fga, tenant_id, store_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("authz_outbox_worker_error")
            await asyncio.sleep(self._interval)


async def mark_all_pending_done(session: AsyncSession) -> None:
    """Used after a full reconcile in seeding: everything pending is already reflected."""
    await session.execute(
        update(AuthzOutbox)
        .where(AuthzOutbox.status == "pending")
        .values(status="done", processed_at=datetime.now(UTC))
    )
