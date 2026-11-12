"""Semantic queries compiled to SQL, checked as SQL and run on the Acme data: the numbers must
match the finance workbook and the sales notes in seed/acme/corpus."""

import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from tests.analytics_support import (
    ACME_ANALYTICS,
    acme_model,
    build_acme,
    csv_rows,
    dan_filters,
    run,
)
from workmind.modules.analytics import dataset, engine, semantic
from workmind.modules.analytics.compiler import (
    CompileError,
    SemanticQuery,
    compile_query,
    duplicate_keys_sql,
    orphan_keys_sql,
)

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
        "FROM invoice_lines WHERE invoice_lines.status <> 'void' GROUP BY 1 ORDER BY 1 ASC"
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
        "LEFT JOIN customers ON invoice_lines.customer_id = customers.customer_id "
        "LEFT JOIN products ON invoice_lines.product_id = products.product_id "
        "WHERE invoice_lines.status <> 'void' AND customers.region = 'DACH' "
        "GROUP BY 1 ORDER BY 2 DESC"
    )


def test_groups_where_no_row_passes_the_metric_filters_are_left_out(acme: Path) -> None:
    # Without the WHERE, won and lost would be listed with no value (NULL).
    assert results(acme, metrics=["pipeline_value"], dimensions=["stage"]) == [
        ["proposal", 1_870_000],
        ["negotiation", 500_000],
        ["qualified", 380_000],
    ]
    assert [r[0] for r in results(acme, metrics=["revenue"], dimensions=["invoice_status"])] == [
        "paid",
        "open",
    ]
    # Different filters: a row passing either one still counts.
    either = compiled(metrics=["revenue", "open_receivables"], dimensions=["invoice_status"])
    assert "WHERE invoice_lines.status <> 'void' OR invoice_lines.status = 'open' GROUP" in either
    # No grouping, or a metric without a filter: nothing is added.
    assert compiled(metrics=["revenue"]).endswith("FROM invoice_lines ORDER BY 1 DESC")
    mixed = compiled(metrics=["won_value", "deals"], dimensions=["deal_owner"])
    assert "FROM opportunities GROUP BY 1" in mixed
    # The values are the same as without it.
    by_owner = results(acme, metrics=["won_value"], dimensions=["deal_owner"])
    assert by_owner == [["diego.alvarez@acme.test", 4_280_000], ["dan@acme.test", 1_062_500]]


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


# --- Joins that break many-to-one -------------------------------------------------------------


def acme_with(root: Path, table: str, extra_rows: str) -> Path:
    """The Acme dataset with rows appended to one table's CSV."""
    sources = []
    for path in sorted((ACME_ANALYTICS / "data").glob("*.csv")):
        data = path.read_bytes()
        if path.stem == table:
            data = data.rstrip(b"\r\n") + b"\r\n" + extra_rows.replace("\n", "\r\n").encode()
        sources.append(dataset.TableSource(name=path.stem, data=data, content_type="text/csv"))
    return dataset.build(root, uuid.UUID(int=2), base=None, put=sources).path


def test_an_orphan_key_stays_in_a_breakdown_as_an_empty_group(tmp_path: Path) -> None:
    # An invoice line for a product the products table lacks.
    path = acme_with(tmp_path, "invoice_lines", "INV-99999,2026-03-01,C001,AMR-999,1,79925,paid\n")

    total = results(path, metrics=["revenue"])[0][0]
    by_line = results(path, metrics=["revenue"], dimensions=["product_line"])

    assert total == 38_900_000 + 79_925
    assert sum(r[1] for r in by_line) == total  # nothing dropped out
    assert [None, 79_925] in by_line


def test_duplicate_keys_on_the_one_side_are_counted(tmp_path: Path) -> None:
    bluefin = next(r for r in csv_rows("customers") if r["customer_id"] == "C001")
    duplicate = ",".join([*list(bluefin.values())[:2], "Retail", *list(bluefin.values())[3:]])
    path = acme_with(tmp_path, "customers", duplicate + "\n")
    joins = {j.right: j for j in MODEL.joins}

    duplicates = engine.execute_sync(
        path, duplicate_keys_sql(joins["customers.customer_id"]), max_rows=1
    )
    clean = engine.execute_sync(path, duplicate_keys_sql(joins["products.product_id"]), max_rows=1)

    assert duplicates.rows == [[1]]
    assert clean.rows == [[0]]


def test_orphan_keys_are_counted(tmp_path: Path) -> None:
    path = acme_with(tmp_path, "invoice_lines", "INV-99999,2026-03-01,C001,AMR-999,1,79925,paid\n")
    joins = {j.right: j for j in MODEL.joins}

    orphans = engine.execute_sync(path, orphan_keys_sql(joins["products.product_id"]), max_rows=1)

    assert orphans.rows == [[1]]


def test_an_open_ended_date_range_compiles() -> None:
    sql = compiled(metrics=["revenue"], date_from="2026-01-01", date_to="9999-12-31")

    assert "invoice_lines.invoice_date >= CAST('2026-01-01' AS DATE)" in sql
    assert "9999" not in sql
