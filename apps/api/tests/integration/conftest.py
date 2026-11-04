"""Integration tests against a real PostgreSQL 18.

They need a superuser DSN in TEST_DATABASE_ADMIN_URL, for example the compose Postgres:
    TEST_DATABASE_ADMIN_URL=postgresql://postgres:postgres@localhost:5432/postgres
`make test` sets it (start Postgres first with `make infra`). Without it, they are skipped.

The fixture builds a throwaway database `workmind_test` with its own owner/app roles,
configured exactly like deploy/postgres/init, and migrates it with Alembic as the owner role,
so the tests exercise the same RLS policies and grants as production.
"""

import asyncio
import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from workmind.core.db import create_engine, create_session_factory
from workmind.core.tenancy import global_transaction, tenant_transaction
from workmind.modules.identity.models import Tenant, User

ADMIN_URL = os.environ.get("TEST_DATABASE_ADMIN_URL")
API_DIR = Path(__file__).resolve().parents[2]
TEST_DB = "workmind_test"
OWNER_ROLE = "workmind_test_owner"
APP_ROLE = "workmind_test_app"
PASSWORD = "test-only-password"

SessionFactory = async_sessionmaker[AsyncSession]


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    here = Path(__file__).parent
    for item in items:
        if here in item.path.parents:
            item.add_marker(pytest.mark.integration)
            if not ADMIN_URL:
                item.add_marker(pytest.mark.skip(reason="set TEST_DATABASE_ADMIN_URL to run"))


@dataclass(frozen=True)
class Database:
    owner_url: str
    app_url: str


def _url(role: str, host: str, port: int) -> str:
    return f"postgresql+asyncpg://{role}:{PASSWORD}@{host}:{port}/{TEST_DB}"


async def _create_database(admin_url: str) -> None:
    try:
        admin = await asyncpg.connect(admin_url)
    except (OSError, asyncpg.PostgresError) as exc:
        raise RuntimeError(
            f"Cannot reach Postgres at TEST_DATABASE_ADMIN_URL ({exc}). Run `make infra` first."
        ) from exc
    try:
        for role in (OWNER_ROLE, APP_ROLE):
            if not await admin.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", role):
                await admin.execute(
                    f"CREATE ROLE {role} LOGIN PASSWORD '{PASSWORD}' NOSUPERUSER NOBYPASSRLS"
                )
        await admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        await admin.execute(f"CREATE DATABASE {TEST_DB} OWNER {OWNER_ROLE}")
    finally:
        await admin.close()

    # Same statements as deploy/postgres/init/01-databases.sh, for the test roles.
    test_db = await asyncpg.connect(urlsplit(admin_url)._replace(path=f"/{TEST_DB}").geturl())
    try:
        await test_db.execute(f"""
            REVOKE ALL ON SCHEMA public FROM PUBLIC;
            GRANT USAGE ON SCHEMA public TO {APP_ROLE};
            ALTER DEFAULT PRIVILEGES FOR ROLE {OWNER_ROLE} IN SCHEMA public
                GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE};
            ALTER DEFAULT PRIVILEGES FOR ROLE {OWNER_ROLE} IN SCHEMA public
                GRANT USAGE, SELECT ON SEQUENCES TO {APP_ROLE};
        """)
    finally:
        await test_db.close()


@pytest.fixture(scope="session")
def database() -> Database:
    assert ADMIN_URL is not None
    asyncio.run(_create_database(ADMIN_URL))
    parts = urlsplit(ADMIN_URL)
    host, port = parts.hostname or "localhost", parts.port or 5432
    db = Database(owner_url=_url(OWNER_ROLE, host, port), app_url=_url(APP_ROLE, host, port))

    # Migrate exactly like production: Alembic, as the owner role, in a separate process.
    env = {**os.environ, "MIGRATIONS_DATABASE_URL": db.owner_url, "DB_APP_ROLE": APP_ROLE}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=API_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"alembic upgrade failed:\n{result.stdout}\n{result.stderr}")
    return db


@pytest.fixture
async def app_sessions(database: Database) -> AsyncIterator[SessionFactory]:
    """Sessions as the runtime role: what the API uses."""
    engine = create_engine(database.app_url)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
async def owner_sessions(database: Database) -> AsyncIterator[SessionFactory]:
    """Sessions as the owner role: what migrations and seeding use."""
    engine = create_engine(database.owner_url)
    yield create_session_factory(engine)
    await engine.dispose()


@dataclass(frozen=True)
class SeededTenant:
    id: uuid.UUID
    slug: str
    user_ids: list[uuid.UUID]


async def seed_tenant(owner_sessions: SessionFactory, name: str) -> SeededTenant:
    slug = f"{name}-{uuid.uuid4().hex[:8]}"  # unique per test: the database is shared
    async with global_transaction(owner_sessions) as session:
        tenant = Tenant(slug=slug, name=name.title())
        session.add(tenant)
        await session.flush()
        tenant_id = tenant.id
    async with tenant_transaction(owner_sessions, tenant_id) as session:
        users = [
            User(tenant_id=tenant_id, email=f"{person}@{name}.test", display_name=person.title())
            for person in ("alice", "bob")
        ]
        session.add_all(users)
        await session.flush()
        return SeededTenant(id=tenant_id, slug=slug, user_ids=[u.id for u in users])


@pytest.fixture
async def two_tenants(owner_sessions: SessionFactory) -> tuple[SeededTenant, SeededTenant]:
    return await seed_tenant(owner_sessions, "acme"), await seed_tenant(owner_sessions, "globex")
