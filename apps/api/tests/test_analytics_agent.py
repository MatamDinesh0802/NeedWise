"""The BI agent with a scripted model on the Acme dataset (DuckDB, no database): tool calls run
through the real compiler, guard and engine as the caller; access is decided before any model
call; refused SQL goes back to the model; unbacked numbers get one repair pass."""

from datetime import date
from pathlib import Path

import pytest

from tests.analytics_support import DAN_POLICY, SALES, LocalAnalytics, build_acme, context
from tests.fakes import FakeLLM, ToolTurn
from workmind.modules.analytics.agent import (
    MAX_QUERIES,
    MAX_STEPS,
    MODEL_DOWN,
    AnswerQuery,
    ChartDraft,
    Composed,
    _chart,
    answer,
)
from workmind.providers.llm import LLMError

CTX = context(SALES)
TODAY = date(2026, 11, 12)
GROWTH_SQL = """
WITH quarters AS (
  SELECT c.name AS customer,
         sum(i.amount_eur) FILTER (WHERE i.invoice_date < DATE '2026-04-01') AS q1,
         sum(i.amount_eur) FILTER (WHERE i.invoice_date >= DATE '2026-07-01') AS q3
  FROM invoice_lines i JOIN customers c ON c.customer_id = i.customer_id
  WHERE i.status <> 'void'
  GROUP BY 1
)
SELECT customer, coalesce(q3, 0) - coalesce(q1, 0) AS growth_eur
FROM quarters ORDER BY growth_eur DESC LIMIT 3
"""
MONTHLY: dict[str, object] = {
    "metrics": ["revenue"],
    "dimensions": ["month", "product_line"],
    "date_from": "2026-01-01",
    "date_to": "2026-09-30",
    "purpose": "Monthly revenue by product line, 2026",
}


@pytest.fixture(scope="module")
def acme_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_acme(tmp_path_factory.mktemp("analytics"))


def model(*composed: Composed | Exception, turns: list[ToolTurn] | None = None) -> FakeLLM:
    return FakeLLM(structured=list(composed), tool_turns=turns or [], tool_text="Done.")


def eur_m(value: float) -> str:
    return f"€{value / 1_000_000:.2f}M"


async def test_a_question_is_answered_with_a_chart_and_verified_numbers(acme_file: Path) -> None:
    analytics = LocalAnalytics(acme_file)
    growth = (await analytics.run_sql(CTX, GROWTH_SQL)).rows
    (first, first_growth), (second, _), (third, _) = growth
    assert isinstance(first_growth, int | float)
    narrative = (
        "Revenue from January to September 2026 was €38.9M [Q2]; FleetOS alone billed "
        f"€1,520,000 in September [Q1].\n\nThe top 3 growth drivers from Q1 to Q3 [Q3]: "
        f"{first} (+{eur_m(first_growth)}), {second} and {third}."
    )
    llm = model(
        Composed(
            narrative=narrative,
            chart=ChartDraft(
                type="stacked_bar",
                query="Q1",
                x="month",
                y=["revenue"],
                series="product_line",
                title="Monthly revenue by product line, 2026",
                y_format="number",
            ),
            main_query="Q1",
        ),
        turns=[
            [("query_metrics", MONTHLY), ("query_metrics", {"metrics": ["revenue"]})],
            [("run_sql", {"sql": GROWTH_SQL, "purpose": "Top 3 growth drivers, Q3 vs Q1"})],
        ],
    )

    result = await answer(
        llm, analytics, CTX, "  Monthly revenue trend this year and the top 3 drivers ", today=TODAY
    )

    assert result.status == "answered"
    assert result.error is None
    assert result.question == "Monthly revenue trend this year and the top 3 drivers"
    assert [q.id for q in result.queries] == ["Q1", "Q2", "Q3"]
    assert [q.purpose for q in result.queries] == [
        "Monthly revenue by product line, 2026",
        "revenue",
        "Top 3 growth drivers, Q3 vs Q1",
    ]
    assert (first, second, third) == ("Bluefin Freight", "Meridian Auto Parts", "Polaris Parcel")
    assert result.queries[1].rows == [[38_900_000]]
    assert ["2026-09-01", "FleetOS", 1_520_000] in result.queries[0].rows
    assert [c.format for c in result.queries[0].columns] == ["date", "text", "eur"]
    assert result.chart is not None
    assert (result.chart.type, result.chart.query, result.chart.series) == (
        "stacked_bar",
        "Q1",
        "product_line",
    )
    assert result.chart.y_format == "eur"  # the column is declared in euros
    assert result.main_query == "Q1"
    assert [(n.text, n.verified, n.query) for n in result.verification.numbers] == [
        ("€38.9M", True, "Q2"),
        ("€1,520,000", True, "Q1"),
        (f"+{eur_m(first_growth)}", True, "Q3"),
    ]
    assert (result.verification.verified, result.verification.total) == (3, 3)
    assert [s.table for s in result.sources] == ["invoice_lines", "products", "customers"]
    assert result.usage.model == "fake"
    assert result.usage.input_tokens == 50 + 50 + 60 + 30
    assert result.usage.cost_usd is None  # no price is listed for the fake model
    # One tool loop and one compose: no repair was needed.
    assert llm.roles() == ["agent", "answer"]
    assert [t.name for t in llm.offered] == ["query_metrics", "run_sql"]
    stored = analytics.runs[result.id]
    assert stored["status"] == "done"
    assert stored["answer"]["narrative"] == narrative
    assert stored["answer"]["created_at"] == result.created_at.isoformat().replace("+00:00", "Z")


async def test_the_prompts_carry_the_model_the_date_and_the_rules(acme_file: Path) -> None:
    analytics = LocalAnalytics(acme_file)
    llm = model(
        Composed(narrative="Revenue was €38.9M [Q1].", chart=None, main_query="Q1"),
        turns=[[("query_metrics", {"metrics": ["revenue"]})]],
    )

    result = await answer(llm, analytics, CTX, "Total revenue?", organization="Acme", today=TODAY)

    (_, loop), (_, compose) = llm.requests
    assert "data analyst of Acme" in loop.system
    assert "Today is 2026-11-12" in loop.system
    assert "runs through 2026-09-30" in loop.system
    assert '"This year" means 2026' in loop.system
    assert "revenue [eur, invoice_lines] = sum(amount_eur) WHERE status <> 'void'" in loop.system
    assert "Compute every number you will state with a query" in loop.system
    assert "Amounts are in euros" in loop.system
    assert "sees only some rows" not in loop.system  # this person has no row limits
    assert loop.messages[0].content == "Total revenue?"
    assert '<result query="Q1"' in compose.messages[0].content
    assert "38900000" in compose.messages[0].content
    assert "Done." in compose.messages[0].content  # the tool loop's notes
    assert result.chart is None
    assert result.main_query == "Q1"


async def test_no_access_means_no_model_call(acme_file: Path) -> None:
    analytics = LocalAnalytics(acme_file, can_query=False)
    llm = model()

    result = await answer(llm, analytics, CTX, "What was revenue in September?")

    assert result.status == "no_access"
    assert result.error == "You do not have access to the analytics data."
    assert (result.narrative, result.queries, result.chart) == ("", [], None)
    assert llm.requests == []
    assert analytics.executed == []
    assert analytics.denied == ["ask"]  # audited like a refused query
    assert result.usage.model_dump() == {
        "model": "",
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
    }
    assert analytics.runs[result.id]["status"] == "done"
    assert analytics.runs[result.id]["answer"]["status"] == "no_access"


async def test_not_set_up_fails_without_a_model_call(acme_file: Path) -> None:
    analytics = LocalAnalytics(acme_file, configured=False)
    llm = model()

    result = await answer(llm, analytics, CTX, "Revenue?")

    assert (result.status, result.error) == (
        "failed",
        "Analytics is not set up for this organization yet.",
    )
    assert llm.requests == []
    assert analytics.runs[result.id]["status"] == "failed"


async def test_refused_and_failing_sql_goes_back_to_the_model(acme_file: Path) -> None:
    analytics = LocalAnalytics(acme_file)
    llm = model(
        Composed(narrative="Acme has 24 customers [Q1].", chart=None, main_query="Q1"),
        turns=[
            [("run_sql", {"sql": "SELECT * FROM read_csv('/etc/passwd')", "purpose": "x"})],
            [("run_sql", {"sql": "SELECT count(*) AS n FROM customers WHERE nope = 1"})],
            [("run_sql", {"sql": "DELETE FROM customers"})],
            [("query_metrics", {"metrics": ["revenue"], "sql": "SELECT 1"})],
            [("query_metrics", {"metrics": ["revenue", "deals"]})],
            [("run_sql", {"sql": "SELECT count(*) AS customers FROM customers"})],
        ],
    )

    result = await answer(llm, analytics, CTX, "How many customers do we have?")

    refused, failed, statement, smuggled, mixed, ok = llm.tool_replies
    assert refused.is_error
    assert refused.content.startswith("query_rejected: The function read_csv() is not allowed")
    assert failed.is_error
    assert failed.content.startswith("query_failed:")
    assert "nope" in failed.content
    assert statement.is_error
    assert statement.content.startswith("query_rejected:")
    assert smuggled.is_error
    assert smuggled.content.startswith("invalid_input: sql: Extra inputs are not permitted")
    assert mixed.is_error
    assert mixed.content.startswith("invalid_query: These metrics come from different tables")
    assert not ok.is_error
    assert ok.content.startswith('<result query="Q1" purpose="SQL query" (1 rows)>')
    assert "columns: customers (number)" in ok.content
    # Only queries that ran are part of the answer, numbered in order.
    assert [(q.id, q.rows) for q in result.queries] == [("Q1", [[24]])]
    assert result.status == "answered"
    assert (result.verification.verified, result.verification.total) == (1, 1)


async def test_unbacked_numbers_get_one_repair_pass(acme_file: Path) -> None:
    analytics = LocalAnalytics(acme_file)
    llm = model(
        Composed(
            narrative="Revenue was €40.1M [Q1], up 12% on last year.", chart=None, main_query="Q1"
        ),
        Composed(narrative="Revenue was €38.9M [Q1].", chart=None, main_query="Q1"),
        turns=[[("query_metrics", {"metrics": ["revenue"]})]],
    )

    result = await answer(llm, analytics, CTX, "Total revenue this year?")

    assert llm.roles() == ["agent", "answer", "answer"]
    _, repair = llm.requests[2]
    assert repair.messages[1].role == "assistant"
    assert "€40.1M" in repair.messages[1].content
    assert "€40.1M, 12%" in repair.messages[2].content
    assert result.narrative == "Revenue was €38.9M [Q1]."
    assert (result.verification.verified, result.verification.total) == (1, 1)
    assert result.usage.input_tokens == 50 + 60 + 30 + 30


async def test_numbers_still_unbacked_after_the_repair_are_reported(acme_file: Path) -> None:
    analytics = LocalAnalytics(acme_file)
    llm = model(
        Composed(narrative="Revenue was €40.1M [Q1].", chart=None, main_query="Q1"),
        Composed(narrative="Revenue was €38.9M [Q1], about €41M.", chart=None, main_query="Q1"),
        turns=[[("query_metrics", {"metrics": ["revenue"]})]],
    )

    result = await answer(llm, analytics, CTX, "Total revenue?")

    assert result.status == "answered"
    assert [(n.text, n.verified) for n in result.verification.numbers] == [
        ("€38.9M", True),
        ("€41M", False),
    ]
    assert len(llm.requests) == 3  # one repair only


async def test_a_failed_repair_keeps_the_first_draft(acme_file: Path) -> None:
    analytics = LocalAnalytics(acme_file)
    llm = model(
        Composed(narrative="Revenue was €40.1M [Q1].", chart=None, main_query="Q1"),
        LLMError("overloaded"),
        turns=[[("query_metrics", {"metrics": ["revenue"]})]],
    )

    result = await answer(llm, analytics, CTX, "Total revenue?")

    assert result.status == "answered"
    assert result.narrative == "Revenue was €40.1M [Q1]."
    assert (result.verification.verified, result.verification.total) == (0, 1)


async def test_row_policies_apply_to_the_agents_queries(acme_file: Path) -> None:
    analytics = LocalAnalytics(acme_file, as_dan=True)
    llm = model(
        Composed(narrative="You own these customers [Q1].", chart=None, main_query="Q1"),
        turns=[
            [
                ("run_sql", {"sql": "SELECT DISTINCT account_owner FROM customers"}),
                ("query_metrics", {"metrics": ["revenue"], "dimensions": ["account_owner"]}),
            ]
        ],
    )

    result = await answer(llm, analytics, CTX, "Whose customers are these?")

    assert result.queries[0].rows == [["dan@acme.test"]]
    assert [row[0] for row in result.queries[1].rows] == ["dan@acme.test"]
    # The SQL shown is the query as written; the policy is added when it runs, every time.
    assert "dan@acme.test" not in result.queries[0].sql
    assert all("dan@acme.test" in sql for sql in analytics.executed)
    (_, loop), (_, compose) = llm.requests
    assert DAN_POLICY in loop.system
    assert "say so when you report totals" in loop.system
    assert DAN_POLICY in compose.system


async def test_semantic_sql_is_shown_with_its_limit_so_it_reruns_the_same(
    acme_file: Path,
) -> None:
    analytics = LocalAnalytics(acme_file)
    top = {"metrics": ["revenue"], "dimensions": ["customer"], "limit": 3}
    llm = model(
        Composed(narrative="Bluefin Freight leads [Q1].", chart=None, main_query="Q1"),
        turns=[[("query_metrics", top)]],
    )

    result = await answer(llm, analytics, CTX, "Top 3 customers?")

    (query,) = result.queries
    assert query.sql.endswith("LIMIT 3")
    assert query.truncated is False  # 3 rows were asked for: the row cap did not cut it
    rerun = await analytics.run_sql(CTX, query.sql)
    assert rerun.rows == query.rows


async def test_a_model_failure_is_a_failed_answer_with_the_queries_so_far(
    acme_file: Path,
) -> None:
    analytics = LocalAnalytics(acme_file)
    llm = model(LLMError("down"), turns=[[("query_metrics", {"metrics": ["revenue"]})]])

    result = await answer(llm, analytics, CTX, "Revenue?")

    assert (result.status, result.error) == ("failed", MODEL_DOWN)
    assert [q.id for q in result.queries] == ["Q1"]
    assert analytics.runs[result.id]["status"] == "failed"


async def test_the_query_budget_is_enforced(acme_file: Path) -> None:
    analytics = LocalAnalytics(acme_file)
    # Six turns of two calls each: twelve queries asked for, ten allowed.
    turns: list[ToolTurn] = [[("query_metrics", {"metrics": ["revenue"]})] * 2] * 6
    llm = model(
        Composed(narrative="Revenue was €38.9M [Q1].", chart=None, main_query="Q1"), turns=turns
    )

    result = await answer(llm, analytics, CTX, "Revenue?")

    assert len(result.queries) == MAX_QUERIES
    assert [r.is_error for r in llm.tool_replies] == [False] * MAX_QUERIES + [True, True]
    assert "No more queries" in llm.tool_replies[-1].content


# --- Charts ---------------------------------------------------------------------------------


def query(*columns: tuple[str, str]) -> AnswerQuery:
    return AnswerQuery.model_validate(
        {
            "id": "Q1",
            "purpose": "p",
            "sql": "SELECT 1",
            "columns": [{"name": n, "type": "BIGINT", "format": f} for n, f in columns],
            "rows": [],
            "row_count": 0,
            "truncated": False,
            "elapsed_ms": 1,
        }
    )


def draft(**changes: object) -> ChartDraft:
    base: dict[str, object] = {
        "type": "line",
        "query": "Q1",
        "x": "month",
        "y": ["revenue"],
        "series": None,
        "title": "Revenue",
        "y_format": "number",
    }
    return ChartDraft.model_validate({**base, **changes})


def test_a_chart_must_fit_its_query() -> None:
    queries = [query(("month", "date"), ("product_line", "text"), ("revenue", "eur"))]

    assert _chart(None, queries) is None
    assert _chart(draft(query="Q9"), queries) is None
    assert _chart(draft(x="week"), queries) is None
    assert _chart(draft(y=["margin"]), queries) is None
    chart = _chart(draft(y=["revenue", "margin", "revenue"], series="nope"), queries)
    assert chart is not None
    assert (chart.y, chart.series, chart.y_format) == (["revenue"], None, "eur")


def test_a_stacked_bar_without_parts_is_a_bar() -> None:
    queries = [query(("month", "date"), ("product_line", "text"), ("revenue", "eur"))]

    chart = _chart(draft(type="stacked_bar"), queries)
    stacked = _chart(draft(type="stacked_bar", series="product_line"), queries)

    assert chart is not None
    assert stacked is not None
    assert (chart.type, stacked.type) == ("bar", "stacked_bar")


def test_a_table_falls_back_to_the_querys_columns() -> None:
    queries = [query(("customer", "text"), ("growth", "number"))]

    chart = _chart(draft(type="table", x="nope", y=[], y_format="eur"), queries)

    assert chart is not None
    assert (chart.x, chart.y, chart.y_format) == ("customer", ["growth"], "eur")


async def test_a_loop_cut_at_the_step_limit_is_still_composed(acme_file: Path) -> None:
    analytics = LocalAnalytics(acme_file)
    turns: list[ToolTurn] = [[("query_metrics", {"metrics": ["revenue"]})]] * (MAX_STEPS + 1)
    llm = model(
        Composed(narrative="Revenue was €38.9M [Q1].", chart=None, main_query="Q1"), turns=turns
    )

    result = await answer(llm, analytics, CTX, "Revenue?")

    assert result.status == "answered"
    assert len(result.queries) == MAX_STEPS
    (_, _), (_, compose) = llm.requests
    assert "stopped at its step limit" in compose.messages[0].content
