"""Tenant isolation is enforced by PostgreSQL itself, not only by application code.

Each test plays the attacker: the runtime role, with one tenant's context, tries to read,
write, move or reference another tenant's data.
"""

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from needwise.core.db import create_session_factory
from needwise.core.tenancy import global_transaction, tenant_transaction
from needwise.modules.identity.models import Department, DepartmentMember, Tenant, User
from tests.integration.conftest import Database, SeededTenant, SessionFactory

TenantPair = tuple[SeededTenant, SeededTenant]


async def test_every_table_with_tenant_id_has_forced_rls_and_a_policy(
    app_sessions: SessionFactory,
) -> None:
    # Guardrail: a future table with a tenant_id column but no RLS fails here.
    async with global_transaction(app_sessions) as session:
        rows = (
            await session.execute(
                text("""
                SELECT c.relname AS table_name,
                       c.relrowsecurity AS enabled,
                       c.relforcerowsecurity AS forced,
                       EXISTS (SELECT 1 FROM pg_policies p
                               WHERE p.schemaname = 'public' AND p.tablename = c.relname)
                         AS has_policy
                FROM information_schema.columns col
                JOIN pg_namespace n ON n.nspname = col.table_schema
                JOIN pg_class c ON c.relnamespace = n.oid AND c.relname = col.table_name
                WHERE col.table_schema = 'public' AND col.column_name = 'tenant_id'
                  AND c.relkind = 'r'
                """)
            )
        ).all()

    assert len(rows) >= 6
    unprotected = [r.table_name for r in rows if not (r.enabled and r.forced and r.has_policy)]
    assert unprotected == []


async def test_runtime_role_is_not_superuser_and_cannot_bypass_rls(
    app_sessions: SessionFactory,
) -> None:
    async with global_transaction(app_sessions) as session:
        row = (
            await session.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            )
        ).one()
    assert (row.rolsuper, row.rolbypassrls) == (False, False)


async def test_reads_only_return_the_current_tenants_rows(
    app_sessions: SessionFactory, two_tenants: TenantPair
) -> None:
    acme, _globex = two_tenants

    async with tenant_transaction(app_sessions, acme.id) as session:
        visible = set((await session.scalars(select(User.id))).all())

    assert visible == set(acme.user_ids)


async def test_no_tenant_context_means_no_rows(
    app_sessions: SessionFactory, two_tenants: TenantPair
) -> None:
    async with global_transaction(app_sessions) as session:
        assert (await session.scalars(select(User))).all() == []


async def test_reused_pooled_connection_fails_closed(
    database: Database, two_tenants: TenantPair
) -> None:
    # After a transaction-local setting, Postgres reports it as '' on the same connection.
    # A single-connection pool guarantees the second transaction reuses that connection.
    engine = create_async_engine(database.app_url, pool_size=1, max_overflow=0)
    sessions = create_session_factory(engine)
    acme, _ = two_tenants
    try:
        async with tenant_transaction(sessions, acme.id) as session:
            assert (await session.scalars(select(User.id))).all()
        async with global_transaction(sessions) as session:
            setting = await session.scalar(text("SELECT current_setting('app.tenant_id', true)"))
            leaked = (await session.scalars(select(User.id))).all()
    finally:
        await engine.dispose()

    assert setting == ""
    assert leaked == []


async def test_cannot_insert_rows_for_another_tenant(
    app_sessions: SessionFactory, two_tenants: TenantPair
) -> None:
    acme, globex = two_tenants

    async def insert_into_globex() -> None:
        async with tenant_transaction(app_sessions, acme.id) as session:
            session.add(User(tenant_id=globex.id, email="mallory@globex.test", display_name="M"))
            await session.flush()

    with pytest.raises(DBAPIError, match="row-level security"):
        await insert_into_globex()


async def test_cannot_move_a_row_to_another_tenant(
    app_sessions: SessionFactory, two_tenants: TenantPair
) -> None:
    acme, globex = two_tenants

    async def move_alice_to_globex() -> None:
        async with tenant_transaction(app_sessions, acme.id) as session:
            await session.execute(
                update(User).where(User.id == acme.user_ids[0]).values(tenant_id=globex.id)
            )

    with pytest.raises(DBAPIError, match="row-level security"):
        await move_alice_to_globex()


async def test_updates_cannot_touch_another_tenants_rows(
    app_sessions: SessionFactory, two_tenants: TenantPair
) -> None:
    acme, globex = two_tenants

    async with tenant_transaction(app_sessions, acme.id) as session:
        result = await session.execute(
            update(User).where(User.id == globex.user_ids[0]).values(display_name="pwned")
        )
    async with tenant_transaction(app_sessions, globex.id) as session:
        name = await session.scalar(select(User.display_name).where(User.id == globex.user_ids[0]))

    assert result.rowcount == 0  # pyright: ignore[reportAttributeAccessIssue]
    assert name == "Alice"


async def test_memberships_cannot_reference_another_tenants_user(
    app_sessions: SessionFactory, two_tenants: TenantPair
) -> None:
    # Foreign-key checks ignore RLS; composite (tenant_id, id) keys close that gap.
    acme, globex = two_tenants

    async def add_globex_user_to_acme_department() -> None:
        async with tenant_transaction(app_sessions, acme.id) as session:
            department = Department(tenant_id=acme.id, slug="hr", name="HR")
            session.add(department)
            await session.flush()
            session.add(
                DepartmentMember(
                    tenant_id=acme.id, department_id=department.id, user_id=globex.user_ids[0]
                )
            )
            await session.flush()

    with pytest.raises(IntegrityError):
        await add_globex_user_to_acme_department()


async def test_runtime_role_cannot_create_or_change_tenants(
    app_sessions: SessionFactory, two_tenants: TenantPair
) -> None:
    acme, _ = two_tenants

    async def create_tenant() -> None:
        async with global_transaction(app_sessions) as session:
            session.add(Tenant(slug="evil-corp", name="Evil"))
            await session.flush()

    async def rename_tenant() -> None:
        async with global_transaction(app_sessions) as session:
            await session.execute(update(Tenant).where(Tenant.id == acme.id).values(name="x"))

    with pytest.raises(DBAPIError, match="permission denied"):
        await create_tenant()
    with pytest.raises(DBAPIError, match="permission denied"):
        await rename_tenant()
