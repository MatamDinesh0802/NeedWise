"""The analytics service on a seeded Acme tenant, against PostgreSQL: who may query, which rows
each person sees (whatever the SQL), auditing, administration, and tenant isolation."""

import json
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import JsonValue
from sqlalchemy import select, text

from tests.analytics_support import ACME_ANALYTICS, csv_rows
from tests.integration.conftest import SeededTenant, SessionFactory, seed_tenant
from workmind.core.context import RequestContext
from workmind.core.tenancy import global_transaction, tenant_transaction
from workmind.modules.analytics import dataset
from workmind.modules.analytics.compiler import SemanticQuery
from workmind.modules.analytics.models import AnalyticsModel
from workmind.modules.analytics.service import Analytics, AnalyticsError, load_model
from workmind.modules.governance.models import AuditEvent
from workmind.modules.identity.models import User
from workmind.modules.identity.service import load_identity
from workmind.seed.analytics_load import seed_analytics
from workmind.seed.apply import seed_database
from workmind.seed.company import load_company

ACME = ACME_ANALYTICS.parent / "company.yaml"
DAN_CUSTOMERS = {
    r["customer_id"] for r in csv_rows("customers") if r["account_owner"] == "dan@acme.test"
}
DAN_LINES = sum(1 for r in csv_rows("invoice_lines") if r["customer_id"] in DAN_CUSTOMERS)
DAN_DEALS = sum(1 for r in csv_rows("opportunities") if r["owner"] == "dan@acme.test")
TOTAL_REVENUE = 38_900_000


@dataclass
class AcmeAnalytics:
    tenant_id: uuid.UUID
    users: dict[str, uuid.UUID]  # first part of the e-mail -> id
    service: Analytics
    root: Path
    sessions: SessionFactory
    owner_sessions: SessionFactory

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

    async def sql(self, name: str, sql: str) -> list[list[JsonValue]]:
        return (await self.service.run_sql(await self.ctx(name), sql)).rows


@pytest.fixture
async def acme(
    owner_sessions: SessionFactory, app_sessions: SessionFactory, tmp_path: Path
) -> AcmeAnalytics:
    spec = load_company(ACME)
    company = spec.company.model_copy(update={"slug": f"acme-{uuid.uuid4().hex[:8]}"})
    seeded = await seed_database(spec.model_copy(update={"company": company}), owner_sessions, {})
    root = tmp_path / "analytics"
    result = await seed_analytics(owner_sessions, seeded.tenant_id, ACME_ANALYTICS, root)
    assert result is not None
    async with tenant_transaction(owner_sessions, seeded.tenant_id) as session:
        users = {u.email.split("@")[0]: u.id for u in await session.scalars(select(User))}
    return AcmeAnalytics(
        tenant_id=seeded.tenant_id,
        users=users,
        service=Analytics(app_sessions, root, max_rows=500),
        root=root,
        sessions=app_sessions,
        owner_sessions=owner_sessions,
    )


async def test_seeding_stores_the_model_and_the_data_once(acme: AcmeAnalytics) -> None:
    again = await seed_analytics(acme.owner_sessions, acme.tenant_id, ACME_ANALYTICS, acme.root)

    assert again is not None
    assert (again.model_updated, again.dataset_updated) == (False, False)
    assert again.tables == {
        "customers": 24,
        "invoice_lines": 478,
        "opportunities": 21,
        "products": 4,
    }
    files = sorted(p.name for p in dataset.tenant_dir(acme.root, acme.tenant_id).iterdir())
    async with tenant_transaction(acme.sessions, acme.tenant_id) as session:
        loaded = await load_model(session, acme.tenant_id)
    assert loaded is not None
    assert loaded.model is not None
    assert files == [f"{loaded.dataset_version}.duckdb"]  # one version, nothing half-written
    assert loaded.model.name == "Acme Robotics finance and sales"


async def test_dan_sees_only_his_customers_invoices_and_deals(acme: AcmeAnalytics) -> None:
    owners = await acme.sql("dan", "SELECT DISTINCT account_owner FROM customers")
    assert owners == [["dan@acme.test"]]
    assert await acme.sql("dan", "SELECT count(*) FROM customers") == [[len(DAN_CUSTOMERS)]]
    assert await acme.sql("dan", "SELECT count(*) FROM invoice_lines") == [[DAN_LINES]]
    assert await acme.sql("dan", "SELECT count(*) FROM opportunities") == [[DAN_DEALS]]
    deal_owners = await acme.sql("dan", "SELECT DISTINCT owner FROM opportunities")
    assert deal_owners == [["dan@acme.test"]]

    # Through joins, subqueries, CTEs and set operations: still only his rows.
    by_owner = await acme.sql(
        "dan",
        "SELECT c.account_owner, count(*) FROM invoice_lines i "
        "JOIN customers c USING (customer_id) GROUP BY 1",
    )
    assert by_owner == [["dan@acme.test", DAN_LINES]]
    assert await acme.sql(
        "dan",
        "SELECT count(*) FROM invoice_lines WHERE customer_id NOT IN "
        "(SELECT customer_id FROM customers WHERE account_owner = 'dan@acme.test')",
    ) == [[0]]
    assert await acme.sql(
        "dan",
        "WITH everyone AS (SELECT customer_id FROM invoice_lines "
        "UNION SELECT customer_id FROM opportunities) SELECT count(*) FROM everyone "
        "WHERE customer_id = 'C001'",
    ) == [[0]]
    lines = await acme.sql("dan", "SELECT DISTINCT customer_id FROM invoice_lines ORDER BY 1")
    assert {str(r[0]) for r in lines} <= DAN_CUSTOMERS

    result = await acme.service.run_semantic(
        await acme.ctx("dan"),
        SemanticQuery(metrics=["revenue"], dimensions=["account_owner"]),
    )
    assert [r[0] for r in result.rows] == ["dan@acme.test"]
    revenue = result.rows[0][1]
    assert isinstance(revenue, int)
    assert 0 < revenue < TOTAL_REVENUE
    assert result.row_policies == ["Own accounts only"]
    assert "dan@acme.test" in result.executed_sql
    assert "dan@acme.test" not in result.sql  # the SQL shown is the query, not the policy
    assert [(c.name, c.format) for c in result.columns] == [
        ("account_owner", "text"),
        ("revenue", "eur"),
    ]

    access = await acme.service.access(await acme.ctx("dan"))
    assert access.can_query
    assert not access.can_manage
    assert access.restricted_tables == ["customers", "invoice_lines", "opportunities"]
    assert access.row_policies[0].startswith("Own accounts only: Account executives see only")


@pytest.mark.parametrize("name", ["diego.alvarez", "carol", "hannah.schmidt", "priya.raman"])
async def test_managers_finance_and_leadership_see_every_row(
    acme: AcmeAnalytics, name: str
) -> None:
    ctx = await acme.ctx(name)

    customers = await acme.service.run_sql(ctx, "SELECT count(*) FROM customers")
    revenue = await acme.service.run_semantic(ctx, SemanticQuery(metrics=["revenue"]))

    assert customers.rows == [[24]]
    assert revenue.rows == [[TOTAL_REVENUE]]
    assert revenue.row_policies == []
    assert (await acme.service.access(ctx)).summary == "You see all rows of the analytics data."


@pytest.mark.parametrize("name", ["bob", "mallory", "erin"])
async def test_people_without_access_are_refused(acme: AcmeAnalytics, name: str) -> None:
    ctx = await acme.ctx(name)

    with pytest.raises(AnalyticsError) as refused:
        await acme.service.run_sql(ctx, "SELECT count(*) FROM products")
    with pytest.raises(AnalyticsError):
        await acme.service.run_semantic(ctx, SemanticQuery(metrics=["revenue"]))

    assert (refused.value.status, refused.value.code) == (403, "analytics_forbidden")
    access = await acme.service.access(ctx)
    assert not access.can_query
    assert access.summary == "You do not have access to the analytics data."


async def test_describe_lists_the_model_and_admins_see_the_yaml(acme: AcmeAnalytics) -> None:
    for_dan = await acme.service.describe(await acme.ctx("dan"))
    for_erin = await acme.service.describe(await acme.ctx("erin"))  # org admin, no data access

    assert [t.name for t in for_dan.tables] == [
        "customers",
        "products",
        "invoice_lines",
        "opportunities",
    ]
    assert {t.name: t.rows for t in for_dan.tables}["invoice_lines"] == 478
    assert "revenue" in [m.name for m in for_dan.metrics]
    assert for_dan.data_through is not None
    assert for_dan.data_through.isoformat() == "2026-09-30"
    assert for_dan.yaml is None
    assert for_erin.yaml == (ACME_ANALYTICS / "semantic.yaml").read_text()
    assert for_erin.problems == []
    assert for_erin.access.can_manage
    assert not for_erin.access.can_query
    for name in ("bob", "mallory"):
        with pytest.raises(AnalyticsError, match="No access"):
            await acme.service.describe(await acme.ctx(name))


async def test_refused_and_failed_queries_say_why(acme: AcmeAnalytics) -> None:
    ctx = await acme.ctx("carol")

    with pytest.raises(AnalyticsError) as rejected:
        await acme.service.run_sql(ctx, "SELECT * FROM read_csv('/etc/passwd')")
    with pytest.raises(AnalyticsError) as failed:
        await acme.service.run_sql(ctx, "SELECT no_such_column FROM customers")
    with pytest.raises(AnalyticsError) as invalid:
        await acme.service.run_semantic(ctx, SemanticQuery(metrics=["revenue", "win_rate"]))

    assert (rejected.value.code, rejected.value.detail) == (
        "query_rejected",
        "The function read_csv() is not allowed.",
    )
    assert failed.value.code == "query_failed"
    assert "no_such_column" in (failed.value.detail or "")
    assert invalid.value.code == "invalid_query"


async def test_results_are_capped(acme: AcmeAnalytics) -> None:
    result = await acme.service.run_sql(
        await acme.ctx("carol"), "SELECT * FROM invoice_lines", max_rows=10
    )

    assert (result.row_count, result.truncated) == (10, True)
    assert result.executed_sql.endswith("LIMIT 11")
    amount = next(c for c in result.columns if c.name == "amount_eur")
    assert (amount.type, amount.format) == ("BIGINT", "eur")


async def test_every_query_is_audited_without_its_rows(acme: AcmeAnalytics) -> None:
    dan = await acme.ctx("dan")
    await acme.service.run_sql(dan, "SELECT name FROM customers")
    with pytest.raises(AnalyticsError):
        await acme.service.run_sql(dan, "DROP TABLE customers")
    with pytest.raises(AnalyticsError):
        await acme.service.run_sql(await acme.ctx("bob"), "SELECT 1")

    async with tenant_transaction(acme.sessions, acme.tenant_id) as session:
        events = (
            await session.scalars(
                select(AuditEvent)
                .where(AuditEvent.action == "analytics.query")
                .order_by(AuditEvent.seq)
            )
        ).all()

    details = [e.details for e in events]
    assert [d["status"] for d in details] == ["ok", "rejected", "denied"]
    ok = details[0]
    assert ok["tables"] == ["customers"]
    assert ok["row_policies"] == ["Own accounts only"]
    assert ok["row_count"] == len(DAN_CUSTOMERS)
    assert len(ok["sql_sha256"]) == 64
    assert len(ok["executed_sha256"]) == 64
    assert events[0].actor_id == acme.users["dan"]
    names = {r["name"] for r in csv_rows("customers")}
    assert not any(name in json.dumps(details) for name in names)  # never the rows


async def test_admins_load_tables_and_versions_switch_atomically(acme: AcmeAnalytics) -> None:
    erin = await acme.ctx("erin")
    async with tenant_transaction(acme.sessions, acme.tenant_id) as session:
        before = await load_model(session, acme.tenant_id)
    assert before is not None
    assert before.dataset_version
    old_file = dataset.dataset_path(acme.root, acme.tenant_id, before.dataset_version)

    info = await acme.service.put_table(
        erin, "targets", b"region,target_eur\nDACH,9000000\nNordics,4000000\n", "text/csv"
    )

    async with tenant_transaction(acme.sessions, acme.tenant_id) as session:
        after = await load_model(session, acme.tenant_id)
    assert after is not None
    assert after.dataset_version != before.dataset_version
    assert info["rows"] == 2
    assert set(after.dataset_tables) == {
        "customers",
        "products",
        "invoice_lines",
        "opportunities",
        "targets",
    }
    assert not old_file.exists()  # removed once the switch committed
    # The model does not list the new table, so it cannot be queried yet; the rest still can.
    with pytest.raises(AnalyticsError, match="refused"):
        await acme.service.run_sql(await acme.ctx("carol"), "SELECT * FROM targets")
    assert await acme.sql("carol", "SELECT count(*) FROM customers") == [[24]]

    await acme.service.delete_table(erin, "targets")
    async with tenant_transaction(acme.sessions, acme.tenant_id) as session:
        dropped = await load_model(session, acme.tenant_id)
    assert dropped is not None
    assert "targets" not in dropped.dataset_tables

    with pytest.raises(AnalyticsError) as not_admin:
        await acme.service.put_table(await acme.ctx("dan"), "x", b"a\n1\n", "text/csv")
    with pytest.raises(AnalyticsError) as bad_file:
        await acme.service.put_table(erin, "x", b"%PDF", "application/pdf")
    with pytest.raises(AnalyticsError) as missing:
        await acme.service.delete_table(erin, "nope")
    assert not_admin.value.status == 403
    assert (bad_file.value.status, bad_file.value.code) == (422, "invalid_table")
    assert missing.value.status == 404


async def test_admins_set_the_model_and_bad_models_are_refused(acme: AcmeAnalytics) -> None:
    erin = await acme.ctx("erin")
    original = (ACME_ANALYTICS / "semantic.yaml").read_text()

    with pytest.raises(AnalyticsError) as not_yaml:
        await acme.service.set_model(erin, "tables: [")
    with pytest.raises(AnalyticsError) as unknown_group:
        await acme.service.set_model(erin, original.replace("group:leadership", "group:board"))
    with pytest.raises(AnalyticsError) as not_admin:
        await acme.service.set_model(await acme.ctx("hannah.schmidt"), original)

    assert (not_yaml.value.status, not_yaml.value.code) == (422, "invalid_model")
    assert "group:board" in (unknown_group.value.detail or "")
    assert not_admin.value.status == 403

    renamed = original.replace(
        "name: Acme Robotics finance and sales", "name: Acme revenue and pipeline"
    )
    description = await acme.service.set_model(erin, renamed)

    assert description.name == "Acme revenue and pipeline"
    async with tenant_transaction(acme.sessions, acme.tenant_id) as session:
        row = await session.scalar(select(AnalyticsModel))
        events = (
            await session.scalars(
                select(AuditEvent.actor_id).where(AuditEvent.action == "analytics.model.updated")
            )
        ).all()
    assert row is not None
    assert row.updated_by == acme.users["erin"]
    assert acme.users["erin"] in events


async def test_analytics_tables_have_forced_rls_and_isolate_tenants(
    acme: AcmeAnalytics, owner_sessions: SessionFactory
) -> None:
    async with global_transaction(acme.sessions) as session:
        rows = (
            await session.execute(
                text(
                    "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE relname IN ('analytics_models', 'analytics_runs') ORDER BY relname"
                )
            )
        ).all()
        policies = (
            await session.execute(
                text(
                    "SELECT tablename FROM pg_policies WHERE policyname = 'tenant_isolation' "
                    "AND tablename IN ('analytics_models', 'analytics_runs') ORDER BY 1"
                )
            )
        ).all()
        without_tenant = (await session.scalars(select(AnalyticsModel))).all()
    assert [tuple(r) for r in rows] == [
        ("analytics_models", True, True),
        ("analytics_runs", True, True),
    ]
    assert [r[0] for r in policies] == ["analytics_models", "analytics_runs"]
    assert without_tenant == []

    other: SeededTenant = await seed_tenant(owner_sessions, "globex")
    async with tenant_transaction(acme.sessions, other.id) as session:
        assert (await session.scalars(select(AnalyticsModel))).all() == []
    globex = RequestContext(
        request_id="test",
        tenant_id=other.id,
        user_id=other.user_ids[0],
        org_role="owner",
        principals=frozenset({f"user:{other.user_ids[0]}", f"org:{other.id}"}),
        auth_method="session",
    )
    assert not (await acme.service.access(globex)).configured
    with pytest.raises(AnalyticsError) as not_configured:
        await acme.service.run_sql(globex, "SELECT count(*) FROM customers")
    assert (not_configured.value.status, not_configured.value.code) == (
        404,
        "analytics_not_configured",
    )
