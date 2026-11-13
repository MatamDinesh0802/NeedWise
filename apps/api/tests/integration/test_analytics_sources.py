"""Analytics on live PostgreSQL and MySQL sources (docs/analytics-sql-sources.md), against the
throwaway shop databases (shop_databases.py) and the Acme people:

- the session is read-only even for a database user who may write, one statement only, the
  server's statement timeout, the row cap (remote.py, called directly: the second lock);
- admins connect a source (the password sealed, never returned), read its catalog (its schemas
  only), and save a model checked against the database;
- Dan sees only his shop accounts through SQL and semantic queries, and selective error oracles
  on every hidden row run and count nothing (the CTE fences);
- attacks through the service are refused and audited; hosts on private networks need the
  operator's setting; a moved source needs its password again; an unreachable source is
  unavailable (the reason for admins only); a tenant's connections are bounded;
- the eval runs on a SQL source with a scripted model that answers with the gold SQL.
"""

import asyncio
import base64
import ipaddress
import os
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import SecretStr
from sqlalchemy import select, text

from tests.eval_analytics_support import DATASETS, gold_oracle
from tests.integration.conftest import SeededTenant, SessionFactory, seed_tenant
from tests.integration.shop_databases import SHOP_YAML, ShopServer
from workmind.core.context import RequestContext
from workmind.core.resources import Resources
from workmind.core.tenancy import global_transaction, tenant_transaction
from workmind.evals.analytics import Table, match
from workmind.evals.datasets import load_analytics
from workmind.evals.runner import Askers, Evaluator
from workmind.modules.analytics import engine, remote
from workmind.modules.analytics.compiler import SemanticQuery
from workmind.modules.analytics.models import AnalyticsSource
from workmind.modules.analytics.rotation import rotate_source_passwords
from workmind.modules.analytics.service import Analytics, AnalyticsError
from workmind.modules.analytics.sources import SourceIn, SourcePatch
from workmind.modules.governance.models import AuditEvent
from workmind.modules.identity.models import User
from workmind.modules.identity.service import load_identity
from workmind.modules.model_catalog.secrets import KeyRing
from workmind.seed.apply import seed_database
from workmind.seed.company import load_company

ACME = SHOP_YAML.parents[2] / "company.yaml"
KEY = "d29ya21pbmQtZGV2LW9ubHktTk9ULUEtU0VDUkVUISE="
LOCAL = (ipaddress.ip_network("127.0.0.0/8"), ipaddress.ip_network("::1/128"))
DAN_CUSTOMERS = {"S01", "S04", "S07", "S10"}
HIDDEN_CUSTOMERS = {"S02", "S03", "S05", "S06", "S08", "S09"}
ITEMS = load_analytics(DATASETS, "analytics_sql.jsonl")


@pytest.fixture(params=["postgres", "mysql"])
def server(request: pytest.FixtureRequest) -> ShopServer:
    return request.getfixturevalue(f"shop_{request.param}")


def target(server: ShopServer, *, writer: bool = False, **changes: Any) -> remote.Target:
    values: dict[str, Any] = {
        "kind": server.kind,
        "address": server.host,
        "host": server.host,
        "port": server.port,
        "database": server.database,
        "user": server.writer if writer else server.reader,
        "password": server.password,
        "tls_mode": "require" if server.kind == "mysql" else "disable",
        **changes,
    }
    return remote.Target(**values)


def run(server: ShopServer, sql: str, *, writer: bool = False, **kw: Any) -> engine.EngineResult:
    return remote.execute(
        target(server, writer=writer), sql, max_rows=kw.get("max_rows", 1000),
        timeout_s=kw.get("timeout_s", 5.0),
    )  # fmt: skip


def count(server: ShopServer, table: str) -> int:
    value = run(server, f"SELECT count(*) FROM shop.{table}", writer=True).rows[0][0]  # noqa: S608
    assert isinstance(value, int)
    return value


# --- The session: the second lock ------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM shop.orders",
        "UPDATE shop.orders SET amount_eur = 0",
        "INSERT INTO shop.internal_notes VALUES (2, 'x')",
        "CREATE TABLE shop.pwned (a INTEGER)",
        "DROP TABLE shop.internal_notes",
    ],
)
def test_the_session_is_read_only_even_for_a_user_who_may_write(
    server: ShopServer, sql: str
) -> None:
    before = count(server, "orders"), count(server, "internal_notes")
    with pytest.raises(engine.QueryFailedError) as failed:
        run(server, sql, writer=True)
    assert failed.value.message == remote._READ_ONLY_MESSAGE, failed.value.message
    assert (count(server, "orders"), count(server, "internal_notes")) == before


def test_one_statement_at_a_time(server: ShopServer) -> None:
    before = count(server, "internal_notes")
    with pytest.raises(engine.QueryFailedError):
        run(server, "SELECT 1; DELETE FROM shop.internal_notes", writer=True)
    assert count(server, "internal_notes") == before


def test_the_server_stops_a_long_query(server: ShopServer) -> None:
    # 87^6 rows, each looked at (MySQL counts a bare cross join without reading it).
    tables = ", ".join(f"shop.orders o{i}" for i in range(6))
    total = " + ".join(f"o{i}.amount_eur" for i in range(6))
    cross = f"SELECT count(*) FROM {tables} WHERE {total} > 1"  # noqa: S608
    started = time.monotonic()
    with pytest.raises(engine.QueryTimeoutError):
        run(server, cross, timeout_s=1.0)
    assert time.monotonic() - started < 4


def test_rows_are_capped_and_typed(server: ShopServer) -> None:
    capped = run(server, "SELECT * FROM shop.orders ORDER BY order_id", max_rows=10)
    assert (len(capped.rows), capped.truncated) == (10, True)
    typed = run(
        server,
        "SELECT order_id, order_date, amount_eur, count(*) AS n FROM shop.orders "
        "WHERE order_id = 'O0001' GROUP BY order_id, order_date, amount_eur",
    )
    assert [c.type for c in typed.columns] == ["VARCHAR", "DATE", "DECIMAL", "BIGINT"]
    assert typed.rows == [["O0001", "2026-01-17", 17271.0, 1]]


def test_the_read_only_role_reads_only_its_schema(server: ShopServer) -> None:
    with pytest.raises(engine.QueryFailedError) as denied:
        run(server, "SELECT * FROM hr.salaries")
    assert "denied" in denied.value.message, denied.value.message


def test_postgres_resolves_nothing_outside_pg_catalog(shop_postgres: ShopServer) -> None:
    with pytest.raises(engine.QueryFailedError) as missing:
        run(shop_postgres, "SELECT count(*) FROM orders")
    assert 'relation "orders" does not exist' in missing.value.message


def test_postgres_fences_must_be_materialized(shop_postgres: ShopServer) -> None:
    """Why the guard writes AS MATERIALIZED: inlined, PostgreSQL 18 plans the policy and the
    query together and evaluates the query's CAST on Diego's orders, an oracle on rows Dan may
    not see. Materialized, the same query counts nothing. (If a later PostgreSQL plans the
    inlined form safely, the first half stops failing; the fence holds either way.)"""
    hidden = run(
        shop_postgres, "SELECT order_id FROM shop.orders WHERE customer_id = 'S02' ORDER BY 1"
    ).rows[:3]
    fence = (
        "WITH f AS {how} (SELECT * FROM shop.orders AS o WHERE o.customer_id IN "
        "(SELECT customer_id FROM shop.customers WHERE account_owner = 'dan@acme.test')) "
        "SELECT count(*) FROM f AS orders WHERE order_id = '{id}' "
        "AND CAST(channel AS INTEGER) > 0"
    )
    leaked = 0
    for (order_id,) in hidden:
        try:
            run(shop_postgres, fence.format(how="NOT MATERIALIZED", id=order_id))
        except engine.QueryFailedError:
            leaked += 1
        assert run(shop_postgres, fence.format(how="MATERIALIZED", id=order_id)).rows == [[0]]
    assert leaked == len(hidden)


def test_mysql_refuses_load_data_local(shop_mysql: ShopServer) -> None:
    with pytest.raises(engine.QueryFailedError):
        run(
            shop_mysql,
            "LOAD DATA LOCAL INFILE '/etc/passwd' INTO TABLE shop.internal_notes",
            writer=True,
        )


def test_a_wrong_password_is_unavailable_with_a_reason(server: ShopServer) -> None:
    with pytest.raises(engine.SourceUnavailableError) as refused:
        remote.execute(target(server, password="wrong"), "SELECT 1", max_rows=1, timeout_s=5)
    assert "refused the user name or password" in refused.value.message
    assert "wrong" not in refused.value.message


# --- The service ----------------------------------------------------------------------------


@dataclass
class Shop:
    tenant_id: uuid.UUID
    users: dict[str, uuid.UUID]
    service: Analytics
    sessions: SessionFactory
    server: ShopServer

    async def ctx(self, name: str) -> RequestContext:
        async with tenant_transaction(self.sessions, self.tenant_id) as session:
            identity = await load_identity(session, self.users[name])
        assert identity is not None
        return RequestContext(
            request_id="test",
            tenant_id=self.tenant_id,
            user_id=identity.user.id,
            org_role=identity.user.org_role,
            principals=identity.principals,
            auth_method="session",
            scopes=frozenset({"*"}),
        )

    def source(self, **changes: Any) -> SourceIn:
        values: dict[str, Any] = {
            "key": "shop",
            "name": f"Shop ({self.server.kind})",
            "kind": self.server.kind,
            "host": self.server.host,
            "port": self.server.port,
            "database": self.server.database,
            "username": self.server.reader,
            "password": SecretStr(self.server.password),
            "tls_mode": "require" if self.server.kind == "mysql" else "disable",
            "schemas": ["shop"],
            **changes,
        }
        return SourceIn.model_validate(values)

    async def connect(self) -> None:
        erin = await self.ctx("erin")
        await self.service.create_source(erin, self.source())
        await self.service.set_source_model(erin, "shop", SHOP_YAML.read_text())

    async def sql(self, name: str, sql: str) -> list[list[Any]]:
        return (await self.service.run_sql(await self.ctx(name), sql, dataset="shop")).rows


async def seed_people(owner_sessions: SessionFactory) -> tuple[uuid.UUID, dict[str, uuid.UUID]]:
    spec = load_company(ACME)
    company = spec.company.model_copy(update={"slug": f"acme-{uuid.uuid4().hex[:8]}"})
    seeded = await seed_database(spec.model_copy(update={"company": company}), owner_sessions, {})
    async with tenant_transaction(owner_sessions, seeded.tenant_id) as session:
        users = {u.email.split("@")[0]: u.id for u in await session.scalars(select(User))}
    return seeded.tenant_id, users


@pytest.fixture
async def shop(
    server: ShopServer, owner_sessions: SessionFactory, app_sessions: SessionFactory, tmp_path: Any
) -> AsyncIterator[Shop]:
    tenant_id, users = await seed_people(owner_sessions)
    pool = engine.QueryPool(workers=2)
    service = Analytics(
        app_sessions, tmp_path, max_rows=500, pool=pool, keyring=KeyRing(KEY),
        source_networks=LOCAL, timeout_s=5.0,
    )  # fmt: skip
    yield Shop(tenant_id, users, service, app_sessions, server)
    pool.close()


async def test_admins_connect_a_source_and_its_password_never_comes_back(shop: Shop) -> None:
    erin, carol = await shop.ctx("erin"), await shop.ctx("carol")
    with pytest.raises(AnalyticsError) as not_admin:
        await shop.service.create_source(carol, shop.source())
    assert not_admin.value.code == "forbidden"

    created = await shop.service.create_source(erin, shop.source())
    listed = await shop.service.sources(erin)
    check = await shop.service.test_source(erin, "shop")

    assert created.password_set
    assert shop.server.password not in created.model_dump_json()
    assert [s.key for s in listed] == ["shop"]
    assert shop.server.password not in listed[0].model_dump_json()
    assert check.ok, check.error
    assert check.server_version
    assert "PostgreSQL" in check.server_version or "8." in check.server_version
    with pytest.raises(AnalyticsError) as taken:
        await shop.service.create_source(erin, shop.source())
    assert taken.value.status == 409
    async with tenant_transaction(shop.sessions, shop.tenant_id) as session:
        row = await session.scalar(select(AnalyticsSource))
        events = (
            await session.scalars(
                select(AuditEvent).where(AuditEvent.action.like("analytics.source.%"))
            )
        ).all()
    assert row is not None
    assert row.password is not None
    assert shop.server.password.encode() not in row.password
    assert [e.action for e in events] == ["analytics.source.created"]
    assert shop.server.password not in str(events[0].details)
    # Not queryable until it has a model.
    assert [d.key for d in await shop.service.datasets(carol)] == []


async def test_the_catalog_lists_only_the_sources_schemas(shop: Shop) -> None:
    erin = await shop.ctx("erin")
    await shop.service.create_source(erin, shop.source())

    catalog = await shop.service.source_catalog(erin, "shop")

    assert {(t.schema_name, t.name) for t in catalog.tables} == {
        ("shop", "customers"),
        ("shop", "orders"),
        ("shop", "internal_notes"),
    }
    orders = next(t for t in catalog.tables if t.name == "orders")
    assert [(c.name, c.model_type) for c in orders.columns] == [
        ("order_id", "text"),
        ("customer_id", "text"),
        ("order_date", "date"),
        ("status", "text"),
        ("channel", "text"),
        ("amount_eur", "decimal"),
    ]


async def test_a_model_is_checked_against_the_database(shop: Shop) -> None:
    erin = await shop.ctx("erin")
    await shop.service.create_source(erin, shop.source())
    yaml_text = SHOP_YAML.read_text()

    for broken, problem in (
        (
            yaml_text.replace(
                "      amount_eur: { type",
                "      discount_eur: { type: decimal }\n      amount_eur: { type",
            ),
            "has no column discount_eur",
        ),
        (yaml_text.replace("shop.customers", "shop.clients"), "is not in the database"),
        (yaml_text.replace("shop.customers", "hr.customers"), "outside the source's schemas"),
        (yaml_text.replace("sum(amount_eur)", "sum(pg_sleep(amount_eur))", 1), "pg_sleep()"),
        (yaml_text.replace("sum(amount_eur)", "sum(my_udf(amount_eur))", 1), "not available"),
        (yaml_text.replace("department:finance", "department:nobody", 1), "matches nobody"),
    ):
        with pytest.raises(AnalyticsError) as refused:
            await shop.service.set_source_model(erin, "shop", broken)
        assert refused.value.code == "invalid_model"
        assert problem in (refused.value.detail or ""), refused.value.detail

    saved = await shop.service.set_source_model(erin, "shop", yaml_text)
    assert saved.yaml == yaml_text
    datasets = await shop.service.datasets(await shop.ctx("carol"))
    assert [(d.key, d.kind, d.dialect) for d in datasets] == [
        ("shop", shop.server.kind, shop.server.kind)
    ]


async def test_dan_sees_only_his_shop_accounts(shop: Shop) -> None:
    await shop.connect()
    dan, diego = await shop.ctx("dan"), await shop.ctx("diego.alvarez")

    assert await shop.sql("dan", "SELECT count(*) FROM customers") == [[4]]
    assert await shop.sql("diego.alvarez", "SELECT count(*) FROM customers") == [[10]]
    ids = await shop.sql("dan", "SELECT DISTINCT customer_id FROM orders ORDER BY 1")
    assert {r[0] for r in ids} == DAN_CUSTOMERS
    joined = await shop.sql(
        "dan",
        "WITH t AS (SELECT customer_id, count(*) AS n FROM orders GROUP BY customer_id) "
        "SELECT c.customer_id FROM customers c LEFT JOIN t ON t.customer_id = c.customer_id "
        "UNION SELECT customer_id FROM orders WHERE customer_id IN "
        "(SELECT customer_id FROM customers)",
    )
    assert {r[0] for r in joined} == DAN_CUSTOMERS
    query = SemanticQuery(metrics=["shop_revenue", "orders_count"], dimensions=["region"])
    mine = await shop.service.run_semantic(dan, query, dataset="shop")
    everyone = await shop.service.run_semantic(diego, query, dataset="shop")
    assert sum(float(cast(float, r[1])) for r in mine.rows) == pytest.approx(380_841.5)
    assert sum(float(cast(float, r[1])) for r in everyone.rows) == pytest.approx(870_066.75)
    assert mine.row_policies == ["Own shop accounts only"]
    assert "_wm_rows_orders" in mine.executed_sql
    assert "_wm_rows" not in mine.sql  # what is shown has the model's names
    monthly = await shop.service.run_semantic(
        diego,
        SemanticQuery(metrics=["shop_revenue"], dimensions=["month"], date_from=date(2026, 1, 1)),
        dataset="shop",
    )
    assert [r[0] for r in monthly.rows][:3] == ["2026-01-01", "2026-02-01", "2026-03-01"]
    assert monthly.rows[0][1] == pytest.approx(123_331.35)
    assert [c.format for c in monthly.columns] == ["date", "eur"]


async def test_error_oracles_on_hidden_rows_run_and_count_nothing(shop: Shop) -> None:
    await shop.connect()
    # A condition that fails on a row whose name is not a number: if the database evaluated it
    # on a row outside Dan's fence, the error (or its absence) would tell him the row exists.
    oracle = (
        "CAST(name AS INTEGER) > 0"
        if shop.server.kind == "postgres"
        else "JSON_EXTRACT(name, '$') IS NOT NULL"
    )
    sql = "SELECT count(*) FROM customers WHERE customer_id = '{id}' AND " + oracle  # noqa: S608
    with pytest.raises(AnalyticsError) as proof:  # the oracle works on rows one may see
        await shop.sql("diego.alvarez", sql.format(id="S02"))
    assert proof.value.code == "query_failed"
    assert "Birch" not in (proof.value.detail or "")  # the value is never quoted
    for customer in sorted(HIDDEN_CUSTOMERS):
        assert await shop.sql("dan", sql.format(id=customer)) == [[0]], customer
    orders = await shop.sql(
        "diego.alvarez",
        "SELECT order_id FROM orders WHERE customer_id IN ('S02', 'S03', 'S05') ORDER BY 1",
    )
    oracle_orders = (
        "SELECT count(*) FROM orders WHERE order_id = '{id}' AND CAST(channel AS INTEGER) > 0"
        if shop.server.kind == "postgres"
        else "SELECT count(*) FROM orders WHERE order_id = '{id}' AND "
        "JSON_EXTRACT(channel, '$') IS NOT NULL"
    )
    for (order_id,) in orders[:10]:
        assert await shop.sql("dan", oracle_orders.format(id=order_id)) == [[0]], order_id


async def test_attacks_through_the_service_are_refused_and_audited(shop: Shop) -> None:
    await shop.connect()
    before = await asyncio.to_thread(count, shop.server, "orders")
    for sql in (
        "SELECT * FROM hr.salaries",
        "SELECT * FROM internal_notes",
        "SELECT * FROM shop.orders",
        "DELETE FROM orders",
        "SELECT 1; DELETE FROM orders",
        "SELECT pg_sleep(5)",
        "SELECT sleep(5)",
        "SELECT * FROM information_schema.tables",
        "WITH d AS (DELETE FROM orders RETURNING *) SELECT * FROM d",
    ):
        with pytest.raises(AnalyticsError) as refused:
            await shop.sql("carol", sql)
        assert refused.value.code == "query_rejected", (sql, refused.value.detail)
    assert await asyncio.to_thread(count, shop.server, "orders") == before
    async with tenant_transaction(shop.sessions, shop.tenant_id) as session:
        events = (
            await session.scalars(select(AuditEvent).where(AuditEvent.action == "analytics.query"))
        ).all()
    assert {e.details["status"] for e in events} == {"rejected"}
    assert {e.details["dataset"] for e in events} == {"shop"}
    with pytest.raises(AnalyticsError) as no_access:
        await shop.sql("bob", "SELECT count(*) FROM orders")
    assert no_access.value.code == "analytics_forbidden"


async def test_private_hosts_need_the_operators_setting(
    server: ShopServer, owner_sessions: SessionFactory, app_sessions: SessionFactory, tmp_path: Any
) -> None:
    tenant_id, users = await seed_people(owner_sessions)
    strict = Analytics(app_sessions, tmp_path, keyring=KeyRing(KEY))  # no private networks
    shop = Shop(tenant_id, users, strict, app_sessions, server)
    erin = await shop.ctx("erin")
    for host in ("127.0.0.1", "localhost", "169.254.169.254", "metadata.google.internal"):
        with pytest.raises(AnalyticsError) as refused:
            await strict.create_source(erin, shop.source(host=host))
        assert refused.value.code == "invalid_source", host


async def test_rotation_moves_source_passwords_to_the_new_key(shop: Shop) -> None:
    await shop.connect()
    new = base64.b64encode(os.urandom(32)).decode()
    rotated = Analytics(
        shop.sessions, shop.service.root, pool=shop.service.pool, keyring=KeyRing(new),
        source_networks=LOCAL,
    )  # fmt: skip

    report = await rotate_source_passwords(shop.sessions, KeyRing(new, [KEY]))
    try:
        assert report.rotated >= 1
        assert report.failed == 0
        # Only the new key opens it now; the old one alone cannot.
        rows = (await rotated.run_sql(await shop.ctx("carol"), "SELECT 1", dataset="shop")).rows
        assert rows == [[1]]
        with pytest.raises(AnalyticsError) as old:
            await shop.sql("carol", "SELECT 1")
        assert old.value.code == "source_unavailable"
        again = await rotate_source_passwords(shop.sessions, KeyRing(new, [KEY]))
        assert (again.rotated, again.current >= 1) == (0, True)
    finally:  # back to the test key for the other tests
        await rotate_source_passwords(shop.sessions, KeyRing(KEY, [new]))
    assert await shop.sql("carol", "SELECT 1") == [[1]]


async def test_a_moved_source_needs_its_password_again(shop: Shop) -> None:
    await shop.connect()
    erin = await shop.ctx("erin")

    with pytest.raises(AnalyticsError) as again:
        await shop.service.update_source(erin, "shop", SourcePatch(username=shop.server.writer))
    assert again.value.code == "password_required"
    renamed = await shop.service.update_source(erin, "shop", SourcePatch(name="Shop orders DB"))
    assert renamed.name == "Shop orders DB"
    assert await shop.sql("carol", "SELECT count(*) FROM customers") == [[10]]
    moved = await shop.service.update_source(
        erin,
        "shop",
        SourcePatch(username=shop.server.writer, password=SecretStr(shop.server.password)),
    )
    assert moved.username == shop.server.writer
    assert await shop.sql("carol", "SELECT count(*) FROM customers") == [[10]]


async def test_an_unreachable_source_is_unavailable_and_admins_see_why(shop: Shop) -> None:
    await shop.connect()
    erin = await shop.ctx("erin")
    await shop.service.update_source(
        erin, "shop", SourcePatch(port=1, password=SecretStr(shop.server.password))
    )

    with pytest.raises(AnalyticsError) as unavailable:
        await shop.sql("carol", "SELECT count(*) FROM customers")
    assert (unavailable.value.status, unavailable.value.code) == (503, "source_unavailable")
    assert shop.server.host not in (unavailable.value.detail or "")  # not for people asking
    (status,) = await shop.service.sources(erin)
    assert status.last_error
    assert f"{shop.server.host}:1" in status.last_error
    check = await shop.service.test_source(erin, "shop")
    assert not check.ok
    assert check.error
    assert "Could not connect" in check.error


async def test_a_tenants_connections_are_bounded(shop: Shop) -> None:
    await shop.connect()
    limited = Analytics(
        shop.sessions, shop.service.root, pool=shop.service.pool, keyring=KeyRing(KEY),
        source_networks=LOCAL, source_connections=1, timeout_s=1.0,
    )  # fmt: skip
    key = f"t:{shop.tenant_id}:analytics:connections"
    lease = await limited.pool._slots.take(key, 1, 30)  # another query holds the only one
    assert lease is not None
    try:
        with pytest.raises(AnalyticsError) as busy:
            await limited.run_sql(await shop.ctx("carol"), "SELECT 1", dataset="shop")
        assert (busy.value.status, busy.value.code) == (429, "analytics_busy")
    finally:
        await limited.pool._slots.give_back(key, lease)
    assert (await limited.run_sql(await shop.ctx("carol"), "SELECT 1", dataset="shop")).rows


async def test_sources_have_forced_rls_and_stay_in_their_tenant(
    shop: Shop, owner_sessions: SessionFactory
) -> None:
    await shop.connect()
    async with global_transaction(shop.sessions) as session:
        row = (
            await session.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE relname = 'analytics_sources'"
                )
            )
        ).one()
        policy = await session.scalar(
            text(
                "SELECT count(*) FROM pg_policies WHERE tablename = 'analytics_sources' "
                "AND policyname = 'tenant_isolation'"
            )
        )
        without_tenant = (await session.scalars(select(AnalyticsSource))).all()
    assert tuple(row) == (True, True)
    assert policy == 1
    assert without_tenant == []
    other: SeededTenant = await seed_tenant(owner_sessions, "globex")
    globex = RequestContext(
        request_id="test",
        tenant_id=other.id,
        user_id=other.user_ids[0],
        org_role="owner",
        principals=frozenset({f"user:{other.user_ids[0]}", f"org:{other.id}"}),
        auth_method="session",
    )
    assert await shop.service.sources(globex) == []
    assert await shop.service.datasets(globex) == []
    with pytest.raises(AnalyticsError) as unknown:
        await shop.service.run_sql(globex, "SELECT 1", dataset="shop")
    assert unknown.value.code == "unknown_dataset"


# --- The eval on a SQL source ---------------------------------------------------------------


async def test_the_eval_items_hold_and_score_on_a_sql_source(shop: Shop) -> None:
    await shop.connect()
    resources = cast(
        Resources,
        SimpleNamespace(db=shop.sessions, llm=gold_oracle(ITEMS), analytics=shop.service),
    )
    slug = await _slug(shop)
    evaluator = Evaluator(resources, Askers(resources, slug), concurrency=3)
    contexts = {p: await evaluator.askers.context(p) for p in {i.user for i in ITEMS}}
    for item in ITEMS:
        ctx = contexts[item.user]
        access = await shop.service.access(ctx, item.dataset)
        assert access.can_query == (item.expect != "no_access"), item.id
        if item.probe_sql:
            with pytest.raises(AnalyticsError) as rejected:
                await shop.service.run_sql(ctx, item.probe_sql, dataset=item.dataset)
            assert rejected.value.code == "query_rejected", item.id
        if item.gold_sql:
            gold = Table.from_result(
                await shop.service.run_sql(ctx, item.gold_sql, dataset=item.dataset)
            )
            assert gold.rows, item.id
            assert match(item, gold, gold).ok, item.id

    run = await evaluator.analytics(ITEMS, today=date(2026, 11, 12))

    assert run.problems == []
    failed = {r.id: r.reason or r.leaks for r in run.records if not r.passed}
    assert failed == {}
    assert sum(len(r.leaks) for r in run.records) == 0
    assert {r.dataset for r in run.records} == {"shop"}


async def _slug(shop: Shop) -> str:
    from workmind.modules.identity.models import Tenant

    async with global_transaction(shop.sessions) as session:
        tenant = await session.get(Tenant, shop.tenant_id)
    assert tenant is not None
    return tenant.slug


async def test_queries_of_one_person_do_not_overlap(shop: Shop) -> None:
    """Two of Carol's queries at once: the second waits for the first (one per person)."""
    await shop.connect()
    carol = await shop.ctx("carol")
    started = time.monotonic()
    results = await asyncio.gather(
        *(
            shop.service.run_sql(carol, "SELECT count(*) FROM orders", dataset="shop")
            for _ in range(2)
        )
    )
    assert [r.rows for r in results] == [[[87]], [[87]]]
    assert time.monotonic() - started < 10
