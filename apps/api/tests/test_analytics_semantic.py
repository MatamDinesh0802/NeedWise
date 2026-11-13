"""The semantic model: what the YAML may say, and the row filters it yields per person."""

import uuid
from datetime import date

import pytest
import yaml

from needwise.modules.analytics import semantic
from tests.analytics_support import (
    ACME_ANALYTICS,
    FINANCE,
    LEADERSHIP,
    SALES,
    SALES_MANAGER,
    acme_model,
    context,
    resolved,
)


def test_the_acme_model_loads() -> None:
    model = acme_model()

    assert model.data_through == date(2026, 9, 30)
    assert set(model.tables) == {"customers", "products", "invoice_lines", "opportunities"}
    assert model.tables["invoice_lines"].time_column == "invoice_date"
    assert model.tables["opportunities"].time_column == "close_date"
    assert model.access == ["department:finance", "department:sales", "group:leadership"]
    assert {"revenue", "win_rate", "pipeline_value", "average_deal_size"} <= set(model.metrics)
    assert {"product_line", "customer", "region", "stage", "deal_owner"} <= set(model.dimensions)
    assert model.metrics["revenue"].format == "eur"
    assert model.metrics["win_rate"].format == "percent"
    assert model.column_format("invoice_lines", "amount_eur") == "eur"


def test_join_paths_go_from_the_fact_table() -> None:
    model = acme_model()

    assert [j.right for j in model.join_path("invoice_lines", "customers")] == [
        "customers.customer_id"
    ]
    assert model.join_path("invoice_lines", "invoice_lines") == []
    with pytest.raises(ValueError, match="cannot be reached"):
        model.join_path("opportunities", "products")
    assert model.dimension_tables("product_line") == {"products"}


def _acme_raw() -> dict[str, object]:
    return yaml.safe_load((ACME_ANALYTICS / "semantic.yaml").read_text())


def _with(change: dict[str, object]) -> str:
    raw = _acme_raw()
    for path, value in change.items():
        target: dict[str, object] = raw
        *parents, last = path.split("/")
        for key in parents:
            target = target[key]  # type: ignore[assignment]
        target[last] = value
    return yaml.safe_dump(raw)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"dimensions/region": {"sql": "region"}}, "qualify region with its table"),
        ({"dimensions/region": {"sql": "customers.regio"}}, "unknown column customers.regio"),
        ({"dimensions/region": {"sql": "sum(customers.region)"}}, "aggregates belong in metrics"),
        (
            {"dimensions/region": {"sql": "(SELECT max(region) FROM customers)"}},
            "not a statement",
        ),
        (
            {"dimensions/region": {"sql": "coalesce((SELECT 1), customers.region)"}},
            "subqueries are not allowed",
        ),
        ({"dimensions/region": {"sql": "getenv('HOME')"}}, "getenv"),
        ({"dimensions/month": {"sql": "customers.region"}}, "reserved for time grains"),
        ({"dimensions/revenue": {"sql": "customers.region"}}, "both a dimension and a metric"),
        ({"metrics/revenue/sql": "amount_eur"}, "must aggregate"),
        ({"metrics/revenue/sql": "sum(customers.amount_eur)"}, "only columns of invoice_lines"),
        ({"metrics/revenue/sql": "sum(amount)"}, "unknown column invoice_lines.amount"),
        ({"metrics/revenue/sql": "sum(amount_eur) OVER ()"}, "window functions"),
        ({"metrics/revenue/sql": "sum(read_csv('/etc/passwd'))"}, "read_csv"),
        ({"metrics/revenue/filter": "sum(amount_eur) > 0"}, "row condition"),
        ({"metrics/revenue/table": "payroll"}, "unknown table payroll"),
        ({"tables/invoice_lines/time_column": "status"}, "is not a date"),
        ({"tables/invoice_lines/time_column": "issued"}, "is not a column"),
        (
            {"tables/pg_secrets": {"description": "x", "columns": {"a": {"type": "text"}}}},
            "reserved",
        ),
        (
            {"tables/Customers": {"description": "x", "columns": {"a": {"type": "text"}}}},
            "identifier",
        ),
        (
            {"joins": [{"left": "invoice_lines.customer", "right": "customers.customer_id"}]},
            "unknown column",
        ),
        ({"access": ["department"]}, "write `org`"),
        ({"access": ["team:sales"]}, "unknown principal key"),
        ({"access": []}, "at least 1"),
        ({"surprise": True}, "Extra inputs"),
    ],
)
def test_invalid_models_are_refused_with_a_reason(change: dict[str, object], message: str) -> None:
    with pytest.raises(semantic.ModelError, match=message):
        semantic.parse(_with(change))


@pytest.mark.parametrize(
    ("condition", "message"),
    [
        ("owner = '{user.email}'", "without quotes"),
        ("owner = {user.email} AND salary > 0", "unknown column salary"),
        ("customer_id IN (SELECT customer_id FROM payroll)", "unknown table payroll"),
        ("customer_id IN (SELECT id FROM customers)", "unknown column id"),
        ("x.owner = {user.email}", "unknown table or alias x"),
        ("customer_id IN (SELECT customer_id FROM main.customers)", "plain name"),
        ("owner = getenv('USER')", "getenv"),
        ("count(*) > 0", "row condition"),
    ],
)
def test_invalid_row_policies_are_refused(condition: str, message: str) -> None:
    raw = _acme_raw()
    raw["row_policies"][0]["filters"]["opportunities"] = condition  # type: ignore[index]

    with pytest.raises(semantic.ModelError, match=message):
        semantic.parse(yaml.safe_dump(raw))


def test_not_yaml_or_not_a_mapping() -> None:
    with pytest.raises(semantic.ModelError, match="not valid YAML"):
        semantic.parse("tables: [")
    with pytest.raises(semantic.ModelError, match="mapping"):
        semantic.parse("- just a list")


def test_a_sales_rep_gets_the_own_accounts_filters() -> None:
    filters = semantic.row_filters(resolved(), context(SALES), "dan@acme.test")

    assert {t: f.sql(dialect="duckdb") for t, f in filters.items()} == {
        "customers": "(customers.account_owner = 'dan@acme.test')",
        "invoice_lines": (
            "(invoice_lines.customer_id IN (SELECT customer_id FROM customers "
            "WHERE account_owner = 'dan@acme.test'))"
        ),
        "opportunities": "(opportunities.owner = 'dan@acme.test')",
    }


@pytest.mark.parametrize(
    "principals",
    [
        (SALES, SALES_MANAGER),  # Diego, VP Sales
        (SALES, LEADERSHIP),
        (SALES, FINANCE),
        (FINANCE,),  # Carol
        (FINANCE, LEADERSHIP),  # Hannah
        (LEADERSHIP,),  # Priya
    ],
)
def test_managers_finance_and_leadership_see_every_row(principals: tuple[str, ...]) -> None:
    ctx = context(*principals)

    assert semantic.can_query(resolved(), ctx)
    assert semantic.row_filters(resolved(), ctx, "x@acme.test") == {}


@pytest.mark.parametrize("principals", [(), ("dept:engineering",), ("group:hr-records",)])
def test_others_cannot_query(principals: tuple[str, ...]) -> None:
    assert not semantic.can_query(resolved(), context(*principals))


def test_policies_on_the_same_table_are_anded_and_user_ids_substituted() -> None:
    raw = _acme_raw()
    raw["row_policies"].append(  # type: ignore[union-attr]
        {
            "name": "Own deals",
            "description": "Also the deals they created.",
            "applies_to": ["department:sales"],
            "filters": {"opportunities": "opportunity_id <> {user.id}"},
        }
    )
    model = semantic.parse(yaml.safe_dump(raw))
    user_id = uuid.UUID(int=7)

    filters = semantic.row_filters(resolved(model), context(SALES, user_id=user_id), "d@x.test")

    assert filters["opportunities"].sql(dialect="duckdb") == (
        f"(opportunities.owner = 'd@x.test') AND (opportunities.opportunity_id <> '{user_id}')"
    )


def test_emails_are_literals_whatever_they_contain() -> None:
    filters = semantic.row_filters(resolved(), context(SALES), "x' OR 1=1 --@acme.test")

    assert filters["customers"].sql(dialect="duckdb") == (
        "(customers.account_owner = 'x'' OR 1=1 --@acme.test')"
    )


def test_principal_keys() -> None:
    for key in ("org", "department:finance", "department-manager:sales", "group:x", "user:a@b"):
        semantic.check_principal_key(key)
    for key in ("org:x", "department:", "dept:finance", "everyone"):
        with pytest.raises(ValueError, match="principal key"):
            semantic.check_principal_key(key)
