"""Semantic queries compiled to SQL, checked as SQL and run on the Acme data: the numbers must
match the finance workbook and the sales notes in seed/acme/corpus."""

from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from tests.analytics_support import ACME_ANALYTICS, acme_model, build_acme, dan_filters, run
from workmind.modules.analytics import semantic
from workmind.modules.analytics.compiler import CompileError, SemanticQuery, compile_query

MODEL = acme_model()
# finance-reports/monthly-revenue-by-product-2026.xlsx, thousands of euros (generate.py)
WORKBOOK = {
    "AMR-200": [1180, 1240, 1350, 1310, 1330, 1370, 1400, 1360, 1490],
    "AMR-500": [1490, 1530, 1740, 1690, 1730, 1780, 1810, 1830, 2010],
    "FleetOS": [1050, 1090, 1130, 1180, 1230, 1280, 1350, 1430, 1520],
}


@pytest.fixture(scope="module")
def acme(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_acme(tmp_path_factory.mktemp("analytics"))


def compiled(**query: Any) -> str:
    return compile_query(MODEL, SemanticQuery.model_validate(query)).sql


def results(acme: Path, row_filters: Any = None, **query: Any) -> list[list[Any]]:
    semantic_query = SemanticQuery.model_validate(query)
    sql = compile_query(MODEL, semantic_query).sql
    return run(acme, sql, row_filters, max_rows=semantic_query.limit).rows


# --- The SQL ----------------------------------------------------------------------------------


def test_a_metric_with_a_filter_and_a_time_grain() -> None:
    assert compiled(metrics=["revenue"], dimensions=["month"]) == (
        "SELECT CAST(DATE_TRUNC('MONTH', invoice_lines.invoice_date) AS DATE) AS month, "
        "SUM(invoice_lines.amount_eur) FILTER(WHERE invoice_lines.status <> 'void') AS revenue "
        "FROM invoice_lines GROUP BY 1 ORDER BY 1 ASC"
    )


def test_only_the_needed_tables_are_joined() -> None:
    sql = compiled(
        metrics=["revenue"],
        dimensions=["product_line"],
        filters=[{"dimension": "region", "values": ["DACH"]}],
    )

    assert sql == (
        "SELECT products.product_line AS product_line, "
        "SUM(invoice_lines.amount_eur) FILTER(WHERE invoice_lines.status <> 'void') AS revenue "
        "FROM invoice_lines "
        "INNER JOIN customers ON invoice_lines.customer_id = customers.customer_id "
        "INNER JOIN products ON invoice_lines.product_id = products.product_id "
        "WHERE customers.region = 'DACH' GROUP BY 1 ORDER BY 2 DESC"
    )


def test_dates_are_inclusive_literals() -> None:
    sql = compiled(metrics=["deals"], date_from="2026-07-01", date_to="2026-09-30")

    assert "opportunities.close_date >= CAST('2026-07-01' AS DATE)" in sql
    assert "opportunities.close_date < CAST('2026-10-01' AS DATE)" in sql


def test_filter_values_are_escaped_literals() -> None:
    sql = compiled(
        metrics=["revenue"],
        filters=[
            {"dimension": "customer", "op": "not_in", "values": ["x' OR '1'='1", "O'Hara"]},
            {"dimension": "segment", "op": "!=", "values": ["Retail"]},
        ],
    )

    assert "NOT customers.name IN ('x'' OR ''1''=''1', 'O''Hara')" in sql
    assert "customers.segment <> 'Retail'" in sql


def test_metrics_with_their_own_filter_clauses_and_a_metric_filter() -> None:
    raw = yaml.safe_load((ACME_ANALYTICS / "semantic.yaml").read_text())
    raw["metrics"]["won_count"] = {
        "table": "opportunities",
        "sql": "count(*) FILTER (WHERE amount_eur > 0)",
        "filter": "stage = 'won'",
        "description": "Won deals with a value.",
    }
    model = semantic.parse(yaml.safe_dump(raw))

    sql = compile_query(model, SemanticQuery(metrics=["won_count", "win_rate"])).sql

    assert (
        "COUNT(*) FILTER(WHERE opportunities.amount_eur > 0 AND opportunities.stage = 'won') "
        "AS won_count" in sql
    )
    assert "COUNT(*) FILTER(WHERE opportunities.stage = 'won')" in sql  # win_rate, qualified


@pytest.mark.parametrize(
    ("query", "message"),
    [
        ({"metrics": ["revenue", "win_rate"]}, "different tables: revenue \\(invoice_lines\\)"),
        ({"metrics": ["profit"]}, "Unknown metric profit. Metrics: revenue"),
        ({"metrics": ["revenue"], "dimensions": ["colour"]}, "Unknown dimension colour"),
        ({"metrics": ["revenue"], "dimensions": ["stage"]}, "cannot be combined with metrics"),
        ({"metrics": ["deals"], "dimensions": ["product_line"]}, "cannot be combined"),
        (
            {"metrics": ["revenue"], "filters": [{"dimension": "month", "values": ["x"]}]},
            "date_from and date_to",
        ),
        (
            {"metrics": ["revenue"], "order_by": [{"field": "customer"}]},
            "Cannot order by customer",
        ),
    ],
)
def test_requests_that_do_not_fit_the_model(query: dict[str, Any], message: str) -> None:
    with pytest.raises(CompileError, match=message):
        compile_query(MODEL, SemanticQuery.model_validate(query))


@pytest.mark.parametrize(
    "query",
    [
        {"metrics": []},
        {"metrics": ["a", "b", "c", "d", "e", "f", "g"]},
        {"metrics": ["revenue"], "dimensions": ["a", "b", "c", "d", "e"]},
        {"metrics": ["revenue", "revenue"]},
        {"metrics": ["revenue"], "limit": 0},
        {"metrics": ["revenue"], "limit": 1001},
        {"metrics": ["revenue"], "filters": [{"dimension": "region", "values": []}]},
        {"metrics": ["revenue"], "filters": [{"dimension": "region", "values": ["a", "b"]}]},
        {
            "metrics": ["revenue"],
            "filters": [{"dimension": "region", "op": "like", "values": ["a"]}],
        },
        {"metrics": ["revenue"], "date_from": "2026-09-01", "date_to": "2026-01-01"},
        {"metrics": ["revenue"], "filters": [{"dimension": "region", "values": [float("nan")]}]},
        {"metrics": ["revenue"], "sql": "SELECT 1"},
    ],
)
def test_malformed_requests_are_refused(query: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        SemanticQuery.model_validate(query)


def test_a_fact_table_without_a_date_cannot_use_grains() -> None:
    raw = yaml.safe_load((ACME_ANALYTICS / "semantic.yaml").read_text())
    raw["metrics"]["catalog_size"] = {
        "table": "products",
        "sql": "count(*)",
        "description": "Products on the price list.",
    }
    model = semantic.parse(yaml.safe_dump(raw))

    with pytest.raises(CompileError, match="no date column"):
        compile_query(model, SemanticQuery(metrics=["catalog_size"], dimensions=["year"]))
    with pytest.raises(CompileError, match="no date column"):
        compile_query(
            model,
            SemanticQuery.model_validate({"metrics": ["catalog_size"], "date_to": "2026-01-01"}),
        )


# --- The numbers --------------------------------------------------------------------------


def test_total_revenue_is_the_workbook_total(acme: Path) -> None:
    assert results(acme, metrics=["revenue"]) == [[38_900_000]]


def test_monthly_revenue_by_product_line_matches_the_workbook_to_the_euro(acme: Path) -> None:
    rows = results(acme, metrics=["revenue"], dimensions=["month", "product_line"], limit=100)

    by_line: dict[str, list[int]] = defaultdict(list)
    for month, line, revenue in rows:
        by_line[line].append(revenue // 1000)
        assert month.startswith("2026-")
    assert dict(by_line) == WORKBOOK


def test_fleetos_in_september(acme: Path) -> None:
    rows = results(
        acme,
        metrics=["revenue"],
        filters=[{"dimension": "product_line", "values": ["FleetOS"]}],
        date_from="2026-09-01",
        date_to="2026-09-30",
    )

    assert rows == [[1_520_000]]


def test_bluefin_is_the_largest_customer(acme: Path) -> None:
    rows = results(acme, metrics=["revenue"], dimensions=["customer"], limit=1)

    assert rows[0][0] == "Bluefin Freight"


def test_bluefin_fleetos_annual_recurring_revenue(acme: Path) -> None:
    rows = results(
        acme,
        metrics=["revenue"],
        filters=[
            {"dimension": "customer", "values": ["Bluefin Freight"]},
            {"dimension": "product_line", "values": ["FleetOS"]},
        ],
        date_from="2026-09-01",
        date_to="2026-09-30",
    )

    assert 12 * rows[0][0] == 2_300_040


def test_q3_closed_exactly_two_deals_one_lost_on_price(acme: Path) -> None:
    rows = results(
        acme,
        metrics=["deals"],
        dimensions=["deal", "stage", "loss_reason"],
        filters=[{"dimension": "stage", "op": "in", "values": ["won", "lost"]}],
        date_from="2026-07-01",
        date_to="2026-09-30",
        order_by=[{"field": "deal", "direction": "asc"}],
    )

    assert rows == [
        ["Harbor Logistics AMR-500 fleet", "lost", "price", 1],
        ["Nordlicht chilled DC (25 AMR-200)", "won", None, 1],
    ]
    assert results(acme, metrics=["win_rate"], date_from="2026-07-01", date_to="2026-09-30") == [
        [50.0]
    ]


def test_kestrel_is_the_largest_open_deal(acme: Path) -> None:
    rows = results(acme, metrics=["pipeline_value"], dimensions=["deal"], limit=1)

    assert rows == [["Kestrel Antwerp pilot and rollout (40 AMR-500)", 1_100_000]]


def test_top_three_growth_drivers_q3_versus_q1(acme: Path) -> None:
    def by_customer(start: str, end: str) -> dict[str, int]:
        rows = results(
            acme,
            metrics=["revenue"],
            dimensions=["customer"],
            date_from=start,
            date_to=end,
            limit=100,
        )
        return {name: revenue for name, revenue in rows}

    q1 = by_customer("2026-01-01", "2026-03-31")
    q3 = by_customer("2026-07-01", "2026-09-30")
    growth = sorted(q3, key=lambda name: q3[name] - q1.get(name, 0), reverse=True)

    assert growth[:3] == ["Bluefin Freight", "Meridian Auto Parts", "Polaris Parcel"]


def test_compiled_queries_get_row_policies_too(acme: Path) -> None:
    everyone = results(acme, metrics=["revenue", "customers_billed"])
    dan = results(acme, dan_filters(), metrics=["revenue", "customers_billed"])
    dan_by_owner = results(acme, dan_filters(), metrics=["revenue"], dimensions=["account_owner"])

    assert everyone == [[38_900_000, 22]]
    assert dan[0][0] < everyone[0][0]
    assert [owner for owner, _ in dan_by_owner] == ["dan@acme.test"]


def test_limit_detects_truncation(acme: Path) -> None:
    query = SemanticQuery(metrics=["revenue"], dimensions=["customer"], limit=3)
    sql = compile_query(MODEL, query).sql

    result = run(acme, sql, max_rows=query.limit)

    assert (len(result.rows), result.truncated) == (3, True)
