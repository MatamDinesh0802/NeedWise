"""Throwaway shop databases for the SQL-source tests (seed/acme/analytics/sql/shop.sql).

PostgreSQL: a database `<TEST_DATABASE_NAME>_shop` on the server of TEST_DATABASE_ADMIN_URL,
with schemas `shop` and `hr`, owned by a writer role, and a reader role granted SELECT on `shop`
only: the read-only role the docs ask admins to create. MySQL (when TEST_MYSQL_URL is set, e.g.
mysql://root:secret@127.0.0.1:33616): databases `shop` and `hr` with the same two users. Both
are dropped, with their roles, when the session ends.
"""

import asyncio
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

import asyncpg
import pytest

ADMIN_URL = os.environ.get("TEST_DATABASE_ADMIN_URL")
TEST_DB = os.environ.get("TEST_DATABASE_NAME", "workmind_test")

SHOP_SQL = Path(__file__).resolve().parents[4] / "seed" / "acme" / "analytics" / "sql" / "shop.sql"
SHOP_YAML = SHOP_SQL.with_name("shop.yaml")
MYSQL_URL = os.environ.get("TEST_MYSQL_URL")
PASSWORD = "test-only-shop-password"


@dataclass(frozen=True)
class ShopServer:
    kind: str  # postgres | mysql
    host: str
    port: int
    database: str
    reader: str
    writer: str
    password: str = PASSWORD


def _statements(script: str) -> list[str]:
    lines = [line for line in script.splitlines() if not line.startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";\n") if s.strip().rstrip(";")]


# --- PostgreSQL ------------------------------------------------------------------------------

PG_DB = f"{TEST_DB}_shop"
PG_READER, PG_WRITER = f"{TEST_DB}_shop_reader", f"{TEST_DB}_shop_writer"


async def _drop_postgres(admin_url: str) -> None:
    admin = await asyncpg.connect(admin_url)
    try:
        await admin.execute(f"DROP DATABASE IF EXISTS {PG_DB} WITH (FORCE)")
        for role in (PG_READER, PG_WRITER):
            await admin.execute(f"DROP ROLE IF EXISTS {role}")
    finally:
        await admin.close()


async def _create_postgres(admin_url: str) -> None:
    await _drop_postgres(admin_url)
    admin = await asyncpg.connect(admin_url)
    try:
        for role in (PG_READER, PG_WRITER):
            await admin.execute(f"CREATE ROLE {role} LOGIN PASSWORD '{PASSWORD}' NOSUPERUSER")
        await admin.execute(f"CREATE DATABASE {PG_DB} OWNER {PG_WRITER}")
    finally:
        await admin.close()
    parts = urlsplit(admin_url)
    shop = await asyncpg.connect(parts._replace(path=f"/{PG_DB}").geturl())
    try:
        await shop.execute(f"""
            REVOKE ALL ON SCHEMA public FROM PUBLIC;
            CREATE SCHEMA shop AUTHORIZATION {PG_WRITER};
            CREATE SCHEMA hr AUTHORIZATION {PG_WRITER};
        """)
    finally:
        await shop.close()
    writer = await asyncpg.connect(
        host=parts.hostname, port=parts.port or 5432, user=PG_WRITER, password=PASSWORD,
        database=PG_DB,
    )  # fmt: skip
    try:
        await writer.execute(SHOP_SQL.read_text())
        # The read-only role the docs ask for: SELECT on the shop schema, nothing else.
        await writer.execute(f"""
            GRANT USAGE ON SCHEMA shop TO {PG_READER};
            GRANT SELECT ON ALL TABLES IN SCHEMA shop TO {PG_READER};
        """)
    finally:
        await writer.close()


@pytest.fixture(scope="session")
def shop_postgres() -> Iterator[ShopServer]:
    if not ADMIN_URL:
        pytest.skip("set TEST_DATABASE_ADMIN_URL")
    asyncio.run(_create_postgres(ADMIN_URL))
    parts = urlsplit(ADMIN_URL)
    host = "127.0.0.1" if parts.hostname in (None, "localhost") else parts.hostname
    yield ShopServer("postgres", host, parts.port or 5432, PG_DB, PG_READER, PG_WRITER)
    asyncio.run(_drop_postgres(ADMIN_URL))


# --- MySQL -----------------------------------------------------------------------------------

MY_READER, MY_WRITER = "wm_shop_reader", "wm_shop_writer"


@pytest.fixture(scope="session")
def shop_mysql() -> Iterator[ShopServer]:
    if not MYSQL_URL:
        pytest.skip("set TEST_MYSQL_URL to test MySQL sources")
    pymysql = pytest.importorskip("pymysql", reason="install the mysql extra")
    parts = urlsplit(MYSQL_URL)
    host, port = parts.hostname or "127.0.0.1", parts.port or 3306
    root = pymysql.connect(
        host=host,
        port=port,
        user=unquote(parts.username or "root"),
        password=unquote(parts.password or ""),
        autocommit=True,
    )

    def run(*statements: str) -> None:
        with root.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)

    def drop() -> None:
        run(
            "DROP DATABASE IF EXISTS shop",
            "DROP DATABASE IF EXISTS hr",
            f"DROP USER IF EXISTS '{MY_READER}'@'%'",
            f"DROP USER IF EXISTS '{MY_WRITER}'@'%'",
        )

    try:
        drop()
        run(
            "CREATE DATABASE shop",
            "CREATE DATABASE hr",
            *_statements(SHOP_SQL.read_text()),
            f"CREATE USER '{MY_READER}'@'%' IDENTIFIED BY '{PASSWORD}'",
            f"CREATE USER '{MY_WRITER}'@'%' IDENTIFIED BY '{PASSWORD}'",
            f"GRANT SELECT ON shop.* TO '{MY_READER}'@'%'",
            f"GRANT ALL ON shop.* TO '{MY_WRITER}'@'%'",
            f"GRANT ALL ON hr.* TO '{MY_WRITER}'@'%'",
        )
        yield ShopServer("mysql", host, port, "shop", MY_READER, MY_WRITER)
    finally:
        drop()
        root.close()
