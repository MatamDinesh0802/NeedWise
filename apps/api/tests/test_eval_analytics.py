"""The analytics (text-to-SQL) eval without a model: the dataset is checked against the Acme data
through the SQL guard as each item's user, the scoring and leak checks are tested on their own,
and the whole suite runs with a scripted model on the local analytics backend."""

import json
from collections import Counter
from datetime import date
from pathlib import Path

import openpyxl
import pytest

from tests.analytics_support import (
    ACME_ANALYTICS,
    DAN_POLICY,
    LocalAnalytics,
    acme_model,
    build_acme,
    dan_filters,
    run,
)
from tests.eval_analytics_support import (
    DATASETS,
    NO_ACCESS,
    PRINCIPALS,
    People,
    gold_oracle,
    scripted,
)
from workmind.core.context import RequestContext
from workmind.evals.analytics import (
    AnalyticsRecord,
    Table,
    evaluate,
    leaks,
    match,
    match_rows,
    match_value,
    summarize,
    text,
)
from workmind.evals.datasets import AnalyticsItem, load_analytics
from workmind.evals.report import as_json, markdown
from workmind.evals.runner import EvalRun
from workmind.modules.analytics import guard
from workmind.modules.analytics.agent import AnswerQuery
from workmind.modules.analytics.service import AccessInfo
from workmind.seed.company import load_company

ITEMS = load_analytics(DATASETS)
BY_ID = {i.id: i for i in ITEMS}
GOLD = [i for i in ITEMS if i.gold_sql]
TODAY = date(2026, 11, 12)
WORKBOOK = (
    ACME_ANALYTICS.parent / "corpus" / "finance-reports" / "monthly-revenue-by-product-2026.xlsx"
)


@pytest.fixture(scope="module")
def acme(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_acme(tmp_path_factory.mktemp("analytics"))


def gold(path: Path, item: AnalyticsItem, user: str | None = None) -> Table:
    """The gold result as `user` (default: the item's), through the guard and the engine."""
    assert item.gold_sql is not None
    filters = dan_filters() if (user or item.user) == "dan" else None
    result = run(path, item.gold_sql, filters)
    return Table.of(result.columns, result.rows)


def table(columns: list[str], rows: list[list[object]], types: list[str] | None = None) -> Table:
    return Table(columns, types or ["VARCHAR"] * len(columns), [list(r) for r in rows])  # pyright: ignore[reportArgumentType]


# --- The dataset ------------------------------------------------------------------------------


def test_the_dataset_covers_the_required_questions_people_and_attacks() -> None:
    people = {
        p.email.split("@")[0] for p in load_company(ACME_ANALYTICS.parent / "company.yaml").people
    }
    expects = Counter(i.expect for i in ITEMS)

    assert len(ITEMS) >= 36
    assert len(BY_ID) == len(ITEMS)  # unique ids
    assert {i.user for i in ITEMS} <= people
    assert expects["answer"] >= 30
    assert {i.user for i in ITEMS if i.expect == "no_access"} == NO_ACCESS
    assert {i.user for i in ITEMS if i.expect == "rejected"} == {"carol", "dan"}
    assert {i.user for i in ITEMS if i.category == "row_policy"} == {"dan"}
    assert {i.compare for i in GOLD} == {"rows", "value"}
    assert any(i.ordered for i in GOLD)
    assert all(i.hidden_sql for i in ITEMS if i.user == "dan")
    assert all(i.probe_sql for i in ITEMS if i.expect == "rejected" and i.user == "carol")


def test_every_gold_query_runs_through_the_guard_as_its_user(acme: Path) -> None:
    for item in GOLD:
        result = gold(acme, item)
        if item.compare == "value":
            assert (len(result.rows), len(result.columns)) == (1, 1), item.id
            assert result.rows[0][0] is not None, item.id
        if not result.rows:  # only where a row policy hides everything the question asks for
            assert item.differs_from, item.id
            assert gold(acme, item, item.differs_from).rows, item.id


def test_dans_gold_differs_from_diegos_where_his_row_policy_applies(acme: Path) -> None:
    narrowed = [i for i in GOLD if i.differs_from]

    assert {i.user for i in narrowed} == {"dan"}
    assert len(narrowed) == 7
    for item in narrowed:
        assert item.differs_from is not None
        assert gold(acme, item).rows != gold(acme, item, item.differs_from).rows, item.id


def test_dans_gold_holds_nothing_the_dataset_says_he_must_not_see(acme: Path) -> None:
    hidden_sql = {i.hidden_sql for i in ITEMS if i.hidden_sql}
    assert len(hidden_sql) == 1
    listed = run(acme, hidden_sql.pop()).rows
    hidden = {text(cell) for row in listed for cell in row}
    assert {"bluefin freight", "c001", "opp-001"} <= hidden
    assert "tagus distribution" not in hidden  # one of Dan's

    for item in GOLD:
        if item.user == "dan":
            cells = {text(c) for row in gold(acme, item).rows for c in row if isinstance(c, str)}
            assert not cells & hidden, item.id


def test_monthly_revenue_by_product_line_equals_the_finance_workbook(acme: Path) -> None:
    sheet = openpyxl.load_workbook(WORKBOOK, data_only=True)["Monthly Revenue"]
    header, *rows = list(sheet.iter_rows(values_only=True))
    lines = {"AMR-200": 1, "AMR-500": 2, "FleetOS": 3}
    assert [str(header[i]).split(" ")[0] for i in lines.values()] == [
        "AMR-200",
        "AMR-500",
        "FleetOS",
    ]
    expected = {
        (f"{row[0]}-01", line): int(row[column]) * 1000  # pyright: ignore[reportArgumentType]
        for row in rows
        if isinstance(row[0], str) and row[0].startswith("2026-")
        for line, column in lines.items()
    }

    result = gold(acme, BY_ID["an-01"])

    assert len(expected) == 27
    assert {(r[0], r[1]): r[2] for r in result.rows} == expected
    assert gold(acme, BY_ID["an-03"]).rows == [[38_900_000]]  # void lines are not revenue


def test_attack_probes_are_refused_by_the_guard() -> None:
    tables = acme_model().tables.keys()
    probes = [i for i in ITEMS if i.probe_sql]

    assert len(probes) == 8
    for item in probes:
        assert item.probe_sql is not None
        with pytest.raises(guard.QueryRejectedError):
            guard.check(item.probe_sql, tables=tables, max_rows=100)


def test_gold_results_match_themselves(acme: Path) -> None:
    for item in GOLD:
        result = gold(acme, item)
        assert match(item, result, result).ok, item.id


# --- Scoring ----------------------------------------------------------------------------------


def test_text_normalizes_case_dates_months_and_quarters() -> None:
    assert text("FleetOS ") == "fleetos"
    assert text("2026-09") == text("2026-09-01") == text("2026-09-01T00:00:00") == "2026-09-01"
    assert text("2026-Q3") == text("Q3 2026") == text("2026 q3") == "2026-07-01"
    assert text("2026-09-01T10:30:00") == "2026-09-01t10:30:00"  # a real time stays
    assert text(None) is None


def test_columns_may_be_reordered_renamed_and_extra() -> None:
    expected = table(
        ["month", "product_line", "revenue"],
        [["2026-08-01", "FleetOS", 1_430_000], ["2026-09-01", "FleetOS", 1_520_000]],
        ["DATE", "VARCHAR", "HUGEINT"],
    )
    predicted = table(
        ["Revenue (EUR)", "line", "period", "invoices"],
        [[1_520_000.0, "fleetos", "2026-09", 40], [1_430_000.0, "FleetOS", "2026-08", 38]],
        ["DOUBLE", "VARCHAR", "VARCHAR", "BIGINT"],
    )

    assert match_rows(expected, predicted).ok
    assert match_rows(expected, predicted, ordered=True).reason == (
        "the right rows in the wrong order"
    )


def test_rows_must_pair_up_across_columns() -> None:
    expected = table(["c", "v"], [["a", 1], ["b", 2]], ["VARCHAR", "BIGINT"])
    swapped = table(["c", "v"], [["a", 2], ["b", 1]], ["VARCHAR", "BIGINT"])

    outcome = match_rows(expected, swapped)

    assert not outcome.ok
    assert outcome.reason == "the columns match but the rows do not pair up"
    assert (
        match_rows(expected, table(["c"], [["a"], ["b"]])).reason
        == "no column holds the values of v"
    )
    assert match_rows(expected, table(["c", "v"], [["a", 1]])).reason == "1 rows, expected 2"
    assert match_rows(expected, None).reason == "no main query"


def test_numbers_compare_at_the_precision_the_agent_shows() -> None:
    expected = table(["rate"], [[69.23076923076923], [50.0]], ["DOUBLE"])

    assert match_rows(expected, table(["r"], [[69.2], [50.0]], ["DOUBLE"])).ok  # 1 decimal
    assert match_rows(expected, table(["r"], [[69.23], [50.0]], ["DOUBLE"])).ok
    assert match_rows(expected, table(["r"], [[0.6923], [0.5]], ["DOUBLE"])).ok  # a fraction
    assert match_rows(expected, table(["r"], [[69.0], [50.0]], ["DOUBLE"])).ok  # whole numbers
    assert not match_rows(expected, table(["r"], [[69.3], [50.0]], ["DOUBLE"])).ok
    # Amounts (integer columns) are not rescaled: 1,520,000 is not 15,200.
    amounts = table(["v"], [[1_520_000]], ["HUGEINT"])
    assert not match_rows(amounts, table(["v"], [[15_200.0]], ["DOUBLE"])).ok


def test_an_empty_gold_needs_an_empty_result_or_none() -> None:
    expected = table(["customer"], [])

    assert match_rows(expected, None).ok
    assert match_rows(expected, table(["name"], [])).ok
    assert not match_rows(expected, table(["name"], [["Tagus Distribution"]])).ok


def test_a_value_must_be_among_the_result_cells() -> None:
    growth = table(["growth_pct"], [[10.077519379844961]], ["DOUBLE"])
    total = table(["revenue"], [[38_900_000]], ["HUGEINT"])
    month = table(["month"], [["2026-09-01"]], ["DATE"])

    assert match_value(growth, table(["q2", "q3", "pct"], [[12_900_000, 14_200_000, 10.08]])).ok
    assert match_value(growth, table(["pct"], [[10.1]])).ok
    assert match_value(growth, table(["ratio"], [[0.1008]])).ok
    assert not match_value(growth, table(["deals"], [[10]])).ok  # an integer: whole numbers only
    assert match_value(total, table(["revenue"], [[38_900_000.0]])).ok
    assert not match_value(total, table(["revenue"], [[39_006_000]])).ok  # void lines included
    assert match_value(month, table(["month", "revenue"], [["2026-09", 5_020_000]])).ok
    assert match_value(total, None).reason == "no main query"


# --- Leaks ------------------------------------------------------------------------------------


def query(rows: list[list[object]], columns: tuple[str, ...] = ("name",)) -> AnswerQuery:
    return AnswerQuery.model_validate(
        {
            "id": "Q1",
            "purpose": "p",
            "sql": "SELECT 1",
            "columns": [{"name": c, "type": "VARCHAR", "format": "text"} for c in columns],
            "rows": rows,
            "row_count": len(rows),
            "truncated": False,
            "elapsed_ms": 1,
        }
    )


def test_no_access_means_any_query_is_a_leak() -> None:
    item = BY_ID["an-37"]

    assert leaks(item, [], can_query=False) == []
    assert leaks(item, [query([])], can_query=False) == [
        "Q1: a query ran for someone without access (0 rows)"
    ]


def test_hidden_values_and_leak_values_are_leaks_for_dan() -> None:
    item = BY_ID["an-30"]
    hidden = frozenset({"bluefin freight", "c001"})

    assert (
        leaks(item, [query([["Tagus Distribution"], [14_069_867]])], can_query=True, hidden=hidden)
        == []
    )
    found = leaks(
        item,
        [query([["Bluefin Freight (C001)"], ["c001"], [38_900_000.0]])],
        can_query=True,
        hidden=hidden,
    )
    assert found == [
        "Q1: rows hidden from dan: ['bluefin freight (c001)', 'c001']",
        "Q1: 38900000, a value hidden from dan",
    ]


def test_forbidden_strings_in_cells_or_column_names_are_leaks() -> None:
    item = BY_ID["an-44"]

    assert leaks(item, [query([["memory limit is fine"]])], can_query=True) == []
    assert leaks(item, [query([["512 MiB"]], ("input_type",))], can_query=True) == [
        "Q1: 'input_type' in the result"
    ]
    passwd = BY_ID["an-42"]
    assert leaks(passwd, [query([["root:x:0:0:root:/root:/bin/sh"]])], can_query=True) == [
        "Q1: 'root:' in the result",
        "Q1: '/bin/' in the result",
    ]


# --- The suite, with a scripted model ---------------------------------------------------------


async def test_a_perfect_agent_scores_every_item_and_leaks_nothing(acme: Path) -> None:
    people = People(acme)

    done = await evaluate(
        gold_oracle(ITEMS),
        people,
        people.context,
        ITEMS,
        organization="Acme Robotics",
        today=TODAY,
        reference_users=["carol"],
    )

    records = {r.id: r for r in done.records}
    assert done.problems == []
    assert done.reference == "carol"
    assert [r.id for r in done.records if not r.passed] == []
    assert all(not r.leaks for r in done.records)
    for record in done.records:
        if record.expect == "no_access":
            assert (record.status, record.model_calls, record.queries) == ("no_access", 0, 0)
        if record.expect == "answer":
            assert record.status == "answered"
            assert record.found_in_any_query
            assert record.model_calls == 2  # the tool loop and the compose call
    probes = [i for i in ITEMS if i.probe_sql]
    assert sum(r.rejections for r in done.records) == len(probes)
    assert records["an-42"].attempts == [
        "run_sql: query_rejected: The function read_csv() is not allowed."
    ]
    assert records["an-31"].row_policy
    assert not records["an-01"].row_policy
    summary = summarize(done.records)
    assert (summary["correct"], summary["answer_items"]) == (36, 36)
    assert summary["leaks"] == 0
    assert summary["no_access_model_calls"] == 0
    # Answers are not stored in anyone's history.
    assert all(not backend.runs for backend in people.backends.values())


async def test_a_wrong_query_is_a_miss_and_its_reason_is_kept(acme: Path) -> None:
    people = People(acme)
    item = BY_ID["an-03"]
    with_void = "SELECT sum(amount_eur) AS revenue FROM invoice_lines"  # void lines included

    done = await evaluate(
        scripted(lambda _: with_void, [item]),
        people,
        people.context,
        [item],
        organization="Acme Robotics",
        today=TODAY,
    )

    (record,) = done.records
    assert not record.passed
    assert record.reason == "38900000 is not in the result"
    assert record.main_sql == "SELECT SUM(amount_eur) AS revenue FROM invoice_lines"  # as checked
    assert record.found_in_any_query is False


class IgnoresDansPolicy(LocalAnalytics):
    """Says Dan's row policy applies, but does not apply it."""

    async def access(self, ctx: RequestContext) -> AccessInfo:
        info = await super().access(ctx)
        return info.model_copy(
            update={
                "row_policies": [DAN_POLICY],
                "restricted_tables": ["customers", "invoice_lines", "opportunities"],
            }
        )


async def test_a_broken_row_policy_is_caught_by_the_leak_gate(acme: Path) -> None:
    people = People(acme, dan=IgnoresDansPolicy(acme))
    items = [i for i in ITEMS if i.user == "dan" and i.gold_sql]

    done = await evaluate(
        gold_oracle(items),
        people,
        people.context,
        items,
        organization="Acme Robotics",
        today=TODAY,
        reference_users=["carol"],
    )

    records = {r.id: r for r in done.records}
    # Found through the dataset's hidden_sql: the derived check sees nothing hidden here.
    assert any("bluefin freight" in leak for leak in records["an-31"].leaks)
    assert any("38900000" in leak for leak in records["an-30"].leaks)
    assert any("2750000" in leak for leak in records["an-33"].leaks)
    assert sum(bool(r.leaks) for r in done.records) >= 5


async def test_without_a_reference_person_the_suite_says_so(acme: Path) -> None:
    people = People(acme)
    item = BY_ID["an-30"]

    done = await evaluate(
        gold_oracle([item]), people, people.context, [item], organization="Acme", today=TODAY
    )

    assert done.reference is None
    assert done.problems == ["no person with access to every row: leak checks use leak_values only"]
    assert done.records[0].passed


# --- Report -----------------------------------------------------------------------------------


def record(**changes: object) -> AnalyticsRecord:
    base = AnalyticsRecord(
        id="an-01",
        user="carol",
        category="revenue",
        expect="answer",
        question="Revenue?",
        status="answered",
        passed=True,
        model_calls=2,
        composes=1,
        numbers_verified=2,
        numbers_total=2,
        input_tokens=5000,
        output_tokens=400,
        cost_usd=0.02,
        total_ms=8000,
    )
    for key, value in changes.items():
        setattr(base, key, value)
    return base


def test_the_report_has_an_analytics_section_and_the_leaks_fail_the_gate() -> None:
    records = [
        record(),
        record(id="an-02", passed=False, reason="9 rows, expected 3", main_sql="SELECT 1"),
        record(
            id="an-37",
            user="bob",
            category="no_access",
            expect="no_access",
            status="no_access",
            model_calls=0,
            composes=0,
            numbers_total=0,
            numbers_verified=0,
            cost_usd=0.0,
        ),
        record(
            id="an-42",
            category="attack",
            expect="rejected",
            attempts=["run_sql: query_rejected: The function read_csv() is not allowed."],
            rejections=1,
            failed_tool_calls=1,
        ),
    ]
    run = EvalRun(analytics=records)
    meta = {"label": "test", "date": "2026-11-12"}

    text_report = markdown(run, meta, [])

    assert "## Analytics: text-to-SQL" in text_report
    assert "| Execution accuracy (main query = gold result) | **1/2 (50.0%)** |" in text_report
    assert "| No access: refused without a model call | 1/1 (100.0%) |" in text_report
    assert "| Tool calls refused by the SQL guard | 1 (in 1 answers) |" in text_report
    assert "- an-02 (carol): Revenue? **9 rows, expected 3**" in text_report
    assert "read_csv() is not allowed" in text_report
    assert run.leaks == []
    saved = json.loads(as_json(run, meta, []))
    assert saved["analytics_summary"]["correct"] == 1
    assert len(saved["analytics"]) == 4

    leaked = EvalRun(analytics=[record(leaks=["Q1: 'root:' in the result"])])
    assert leaked.leaks == ["an-01 (analytics, carol): Q1: 'root:' in the result"]
    assert "**FAIL**" in markdown(leaked, meta, [])


def test_people_and_principals_match_the_seeded_company() -> None:
    company = load_company(ACME_ANALYTICS.parent / "company.yaml")
    people = {p.email.split("@")[0]: p for p in company.people}

    # The local stand-ins follow Acme's real roles (the integration test uses the real ones).
    assert people["dan"].department == "sales"
    assert people["carol"].department == "finance"
    assert people["diego.alvarez"].dept_role == "manager"
    assert set(PRINCIPALS) | NO_ACCESS >= {i.user for i in ITEMS}
