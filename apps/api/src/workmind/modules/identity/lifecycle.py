"""Creating and deleting a tenant across every store (ARCHITECTURE section 8,
docs/multi-tenancy.md). Used by `wm tenant create|delete|verify-empty`.

Deletion runs in an order that can be repeated after a failure: the tenant is suspended first
(its sessions stop, sign-in and API tokens are refused, background tasks skip it), then its data
leaves every external store, and the PostgreSQL row goes last: deleting it cascades to every
tenant table, and until then the row still names the OpenFGA store and the slug, so a second
run finds what the first left. `verify_empty` then checks every layer and lists what remains.

Everything here runs with the owner database role (MIGRATIONS_DATABASE_URL): the runtime role
can neither create nor delete tenants.
"""

import json
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import structlog
from redis.asyncio import Redis
from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from workmind.core.tenancy import global_transaction, tenant_key_pattern, tenant_transaction
from workmind.modules.analytics import dataset
from workmind.modules.authz.openfga import OpenFgaClient
from workmind.modules.authz.sync import ensure_store, reconcile
from workmind.modules.governance.audit import Actor, record_event
from workmind.modules.identity.models import SLUG_PATTERN, Tenant, User
from workmind.modules.identity.sessions import SessionData, SessionStore
from workmind.modules.knowledge.storage import ObjectStorage, tenant_prefix
from workmind.modules.retrieval.qdrant import QdrantClient, QdrantError
from workmind.seed.keycloak_admin import KeycloakAdmin

log = structlog.get_logger("workmind.tenants")
SessionFactory = async_sessionmaker[AsyncSession]
_SLUG = re.compile(SLUG_PATTERN)


class TenantError(ValueError):
    """A request the lifecycle refuses; the message says why."""


@dataclass
class Stores:
    """Every place a tenant's data lives. `owner`: sessions as the owner database role."""

    owner: SessionFactory
    fga: OpenFgaClient
    qdrant: QdrantClient
    storage: ObjectStorage
    analytics_root: Path
    valkey: Redis
    keycloak: KeycloakAdmin | None = None  # only when the deployment manages Keycloak with WorkMind


@dataclass(frozen=True)
class CreatedTenant:
    id: uuid.UUID
    slug: str
    store_id: str
    owner_id: uuid.UUID | None
    keycloak_org: str | None


@dataclass
class Remaining:
    """What is left of a tenant, per layer. Empty: nothing of the tenant remains."""

    items: dict[str, list[str]] = field(default_factory=dict[str, list[str]])

    def add(self, layer: str, item: str) -> None:
        self.items.setdefault(layer, []).append(item)

    @property
    def empty(self) -> bool:
        return not self.items

    def lines(self) -> list[str]:
        return [f"{layer}: {item}" for layer, items in sorted(self.items.items()) for item in items]


@dataclass(frozen=True)
class DeletionReport:
    tenant_id: uuid.UUID
    slug: str
    store_id: str | None
    sessions: int
    keys: int
    files: int
    collections: list[str]
    keycloak_users: int
    remaining: Remaining


# --- Create ---------------------------------------------------------------------------------------


async def create_tenant(
    stores: Stores,
    *,
    slug: str,
    name: str,
    owner_email: str | None = None,
    owner_name: str | None = None,
    domain: str | None = None,
) -> CreatedTenant:
    """The tenant row, its OpenFGA store and model, optionally its first owner (invited: linked
    at their first sign-in with that verified email) and its Keycloak organization (alias =
    slug, which is how a sign-in finds the tenant)."""
    if not _SLUG.fullmatch(slug) or len(slug) > 63:
        raise TenantError("slug: lower-case letters, digits and single hyphens (at most 63)")
    if not name.strip():
        raise TenantError("name: required")
    if stores.keycloak is not None and await stores.keycloak.find_organization(slug):
        # Left by an earlier tenant (deleted without --keycloak, or by hand): its members would
        # sign in to the new tenant, since the organization alias is how a sign-in finds it.
        raise TenantError(
            f"Keycloak already has an organization with alias {slug!r}; its members would sign "
            "in to the new tenant. Delete that organization in Keycloak or choose another slug"
        )
    async with global_transaction(stores.owner) as session:
        if await session.scalar(select(Tenant.id).where(Tenant.slug == slug)) is not None:
            raise TenantError(f"a tenant with slug {slug!r} already exists")
        tenant = Tenant(slug=slug, name=name.strip(), settings={"restricted_data": "local_only"})
        session.add(tenant)
        await session.flush()
        tenant_id = tenant.id
    owner_id: uuid.UUID | None = None
    async with tenant_transaction(stores.owner, tenant_id) as session:
        if owner_email:
            owner = User(
                tenant_id=tenant_id,
                email=owner_email.strip().lower(),
                display_name=(owner_name or owner_email).strip(),
                org_role="owner",
                status="invited",
            )
            session.add(owner)
            await session.flush()
            owner_id = owner.id
        await record_event(
            session,
            tenant_id=tenant_id,
            actor=Actor.system(),
            action="tenant.created",
            resource_type="tenant",
            resource_id=str(tenant_id),
            details={"slug": slug, "owner": owner_email},
        )
    store_id = await ensure_store(stores.owner, stores.fga, tenant_id)
    await reconcile(stores.owner, stores.fga, tenant_id, store_id)
    org: str | None = None
    if stores.keycloak is not None:
        org = await stores.keycloak.ensure_organization(
            alias=slug, name=name.strip(), domain=domain or f"{slug}.invalid"
        )
    log.info("tenant_created", tenant_id=str(tenant_id), slug=slug)
    return CreatedTenant(tenant_id, slug, store_id, owner_id, org)


# --- Delete ---------------------------------------------------------------------------------------


async def find_tenant(owner: SessionFactory, ref: str) -> Tenant | None:
    """A tenant by slug or by id."""
    async with global_transaction(owner) as session:
        try:
            by_id = uuid.UUID(ref)
        except ValueError:
            return await session.scalar(select(Tenant).where(Tenant.slug == ref))
        return await session.get(Tenant, by_id)


async def delete_tenant(stores: Stores, ref: str) -> DeletionReport:
    tenant = await find_tenant(stores.owner, ref)
    if tenant is None:
        raise TenantError(f"no tenant {ref!r}")
    tenant_id, slug, store_id = tenant.id, tenant.slug, tenant.openfga_store_id

    # 1. Suspend: sign-in, sessions, tokens and background tasks stop for this tenant.
    async with global_transaction(stores.owner) as session:
        await session.execute(
            update(Tenant).where(Tenant.id == tenant_id).values(status="suspended")
        )
    async with tenant_transaction(stores.owner, tenant_id) as session:
        subjects = [s for s in await session.scalars(select(User.keycloak_sub)) if s]
    sessions = await SessionStore(
        stores.valkey, idle_timeout_s=1, absolute_timeout_s=1
    ).delete_by_tenant(tenant_id)
    sessions += await _delete_sessions_of(stores.valkey, tenant_id)  # unindexed (older) ones

    # 2. Search index points, in every collection (one per embedding model).
    collections = await _delete_points(stores.qdrant, tenant_id)

    # 3. Files: originals and recordings, then analytics datasets.
    files = await stores.storage.delete_prefix(tenant_prefix(tenant_id))
    analytics_dir = dataset.tenant_dir(stores.analytics_root, tenant_id)
    files += (
        sum(1 for p in analytics_dir.rglob("*") if p.is_file()) if analytics_dir.exists() else 0
    )
    dataset.remove_tenant(stores.analytics_root, tenant_id)

    # 4. Authorization: every tuple, then the store itself (OpenFGA keeps only a deleted marker).
    if store_id and await stores.fga.store_exists(store_id):
        tuples = await stores.fga.read_all(store_id)
        await stores.fga.write(store_id, deletes=tuples)
        await stores.fga.delete_store(store_id)

    # 5. Valkey: everything under the tenant's prefix.
    keys = await _delete_keys(stores.valkey, tenant_key_pattern(tenant_id))

    # 6. Keycloak, when WorkMind manages it: the organization, and the users that belonged to
    #    no other organization (users in several organizations are shared with other tenants).
    keycloak_users = 0
    if stores.keycloak is not None:
        keycloak_users = await delete_keycloak_organization(stores.keycloak, slug, subjects)

    # 7. PostgreSQL: the row; every tenant table follows through ON DELETE CASCADE.
    async with global_transaction(stores.owner) as session:
        await session.execute(delete(Tenant).where(Tenant.id == tenant_id))
    log.info("tenant_deleted", tenant_id=str(tenant_id), slug=slug)

    remaining = await verify_empty(stores, tenant_id, slug=slug, store_id=store_id)
    return DeletionReport(
        tenant_id=tenant_id,
        slug=slug,
        store_id=store_id,
        sessions=sessions,
        keys=keys,
        files=files,
        collections=collections,
        keycloak_users=keycloak_users,
        remaining=remaining,
    )


async def _delete_points(qdrant: QdrantClient, tenant_id: uuid.UUID) -> list[str]:
    touched: list[str] = []
    for name in await qdrant.list_collections():
        try:
            if await qdrant.count(name, tenant_id=tenant_id):
                await qdrant.delete(name, tenant_id=tenant_id)
                touched.append(name)
        except QdrantError:
            if await qdrant.collection_exists(name):  # gone meanwhile: nothing to delete
                raise
    return touched


async def _delete_keys(valkey: Redis, pattern: str) -> int:
    deleted = 0
    batch: list[bytes] = []
    async for key in valkey.scan_iter(pattern, count=1000):  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        batch.append(key)  # pyright: ignore[reportUnknownArgumentType]
        if len(batch) >= 500:
            deleted += int(await valkey.delete(*batch))
            batch = []
    if batch:
        deleted += int(await valkey.delete(*batch))
    return deleted


async def _sessions_of(valkey: Redis, tenant_id: uuid.UUID) -> list[bytes]:
    """Session keys whose data names the tenant (a scan of every session: deletion only)."""
    found: list[bytes] = []
    async for key in valkey.scan_iter("session:*", count=1000):  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        raw = await valkey.get(key)  # pyright: ignore[reportUnknownArgumentType]
        if raw is None:
            continue
        try:
            if SessionData.from_json(raw).tenant_id == tenant_id:
                found.append(key)  # pyright: ignore[reportUnknownArgumentType]
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            continue
    return found


async def _delete_sessions_of(valkey: Redis, tenant_id: uuid.UUID) -> int:
    keys = await _sessions_of(valkey, tenant_id)
    return int(await valkey.delete(*keys)) if keys else 0


async def delete_keycloak_organization(
    keycloak: KeycloakAdmin, slug: str, subjects: Sequence[str]
) -> int:
    org = await keycloak.find_organization(slug)
    members = set(subjects)
    if org is not None:
        members |= set(await keycloak.organization_members(org))
    deleted = 0
    for user_id in sorted(members):
        others = [o for o in await keycloak.user_organizations(user_id) if o != org]
        if not others:
            await keycloak.delete_user(user_id)
            deleted += 1
    if org is not None:
        await keycloak.delete_organization(org)
    return deleted


# --- Verify ---------------------------------------------------------------------------------------


async def tenant_tables(session: AsyncSession) -> list[str]:
    """Every table with a tenant_id column (the same rule as the RLS guardrail test)."""
    return list(
        await session.scalars(
            text(
                "SELECT c.table_name FROM information_schema.columns c "
                "JOIN information_schema.tables t USING (table_schema, table_name) "
                "WHERE c.table_schema = 'public' AND c.column_name = 'tenant_id' "
                "AND t.table_type = 'BASE TABLE' ORDER BY 1"
            )
        )
    )


async def verify_empty(
    stores: Stores, tenant_id: uuid.UUID, *, slug: str | None = None, store_id: str | None = None
) -> Remaining:
    """What remains of the tenant in every layer. With `slug`, also OpenFGA stores named after
    it and its Keycloak organization; with `store_id`, that store."""
    remaining = Remaining()
    async with global_transaction(stores.owner) as session:
        if await session.get(Tenant, tenant_id) is not None:
            remaining.add("postgres", "tenants row")
    # FORCE ROW LEVEL SECURITY applies to the owner role too: count inside the tenant.
    async with tenant_transaction(stores.owner, tenant_id) as session:
        for table in await tenant_tables(session):
            count = await session.scalar(
                text(f'SELECT count(*) FROM "{table}" WHERE tenant_id = :t'),  # noqa: S608 (catalog names)
                {"t": tenant_id},
            )
            if count:
                remaining.add("postgres", f"{table}: {count} rows")
    for name in await stores.qdrant.list_collections():
        try:
            if points := await stores.qdrant.count(name, tenant_id=tenant_id):
                remaining.add("qdrant", f"{name}: {points} points")
        except QdrantError:
            if await stores.qdrant.collection_exists(name):
                raise
    for key in await stores.storage.list_keys(tenant_prefix(tenant_id)):
        remaining.add("object storage", key)
    analytics_dir = dataset.tenant_dir(stores.analytics_root, tenant_id)
    if analytics_dir.exists():
        remaining.add("analytics", str(analytics_dir))
    async for key in stores.valkey.scan_iter(tenant_key_pattern(tenant_id), count=1000):  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        remaining.add("valkey", key.decode())  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
    for key in await _sessions_of(stores.valkey, tenant_id):
        remaining.add("valkey", key.decode())
    if store_id and await stores.fga.store_exists(store_id):
        remaining.add("openfga", f"store {store_id}")
    async with global_transaction(stores.owner) as session:
        reused = slug and await session.scalar(select(Tenant.id).where(Tenant.slug == slug))
    if slug and not reused:  # a new tenant with the same slug owns those names now
        for found in await stores.fga.stores_named(f"tenant-{slug}"):
            if found != store_id:
                remaining.add("openfga", f"store {found} (tenant-{slug})")
        if stores.keycloak is not None and await stores.keycloak.find_organization(slug):
            remaining.add("keycloak", f"organization {slug}")
    return remaining
