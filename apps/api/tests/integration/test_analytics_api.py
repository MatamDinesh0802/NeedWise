"""The analytics API over HTTP on a seeded Acme tenant (PostgreSQL, Valkey, DuckDB files in a
temporary directory) with a scripted model: who may query, which rows each person gets through
/ask and /query, each person's own runs, and administration of the model and the data."""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.analytics_support import ACME_ANALYTICS, csv_rows
from tests.fakes import FakeLLM, ToolTurn
from tests.integration.conftest import VALKEY_URL, Acme, Database, SessionFactory, seed_acme
from workmind.core.config import Settings
from workmind.core.tenancy import tenant_transaction
from workmind.main import create_app
from workmind.modules.analytics.agent import ChartDraft, Composed
from workmind.modules.governance.models import AuditEvent
from workmind.seed.analytics_load import seed_analytics

pytestmark = pytest.mark.skipif(not VALKEY_URL, reason="set TEST_VALKEY_URL")
MAX_UPLOAD = 200_000
DAN_CUSTOMERS = {
    r["customer_id"] for r in csv_rows("customers") if r["account_owner"] == "dan@acme.test"
}
DAN_LINES = sum(1 for r in csv_rows("invoice_lines") if r["customer_id"] in DAN_CUSTOMERS)
TARGETS = b"month,target_eur\n2026-10-01,5200000\n2026-11-01,5400000\n2026-12-01,6100000\n"


@pytest.fixture
def api(database: Database, tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        app_env="test",
        database_url=database.app_url,
        valkey_url=VALKEY_URL or "",
        authz_outbox_worker=False,
        object_storage_path=str(tmp_path),
        max_upload_bytes=MAX_UPLOAD,
    )
    with TestClient(create_app(settings)) as client:
        yield client


@pytest.fixture
async def acme(owner_sessions: SessionFactory, tmp_path: Path) -> Acme:
    seeded = await seed_acme(owner_sessions)
    loaded = await seed_analytics(
        owner_sessions, seeded.tenant_id, ACME_ANALYTICS, tmp_path / "analytics"
    )
    assert loaded is not None
    return seeded


def use_model(api: TestClient, *composed: Composed, turns: list[ToolTurn]) -> FakeLLM:
    llm = FakeLLM(structured=list(composed), tool_turns=turns)
    app: Any = api.app
    app.state.resources.llm = llm
    return llm


def ask(api: TestClient, acme: Acme, who: str, question: str) -> dict[str, Any]:
    response = api.post(
        "/api/v1/analytics/ask", json={"question": question}, headers=acme.as_(who, write=True)
    )
    assert response.status_code == 200, response.text
    return response.json()


def query(api: TestClient, acme: Acme, who: str, sql: str) -> Any:
    return api.post("/api/v1/analytics/query", json={"sql": sql}, headers=acme.as_(who, write=True))


# --- The model --------------------------------------------------------------------------------


async def test_the_model_for_people_who_may_query(api: TestClient, acme: Acme) -> None:
    carol = api.get("/api/v1/analytics/model", headers=acme.as_("carol"))
    dan = api.get("/api/v1/analytics/model", headers=acme.as_("dan")).json()

    assert carol.status_code == 200, carol.text
    body = carol.json()
    assert body["available"] is True
    assert body["name"] == "Acme Robotics finance and sales"
    assert body["data_through"] == "2026-09-30"
    metrics = {m["name"]: m for m in body["metrics"]}
    assert set(metrics["revenue"]) == {"name", "description", "format"}
    assert (metrics["revenue"]["format"], metrics["win_rate"]["format"]) == ("eur", "percent")
    assert metrics["deals"]["format"] == "number"
    dimensions = {d["name"] for d in body["dimensions"]}
    assert {"month", "quarter", "year", "region", "product_line"} <= dimensions
    assert [t["name"] for t in body["tables"]] == [
        "customers",
        "products",
        "invoice_lines",
        "opportunities",
    ]
    columns = body["tables"][2]["columns"]
    assert {"name": "amount_eur", "type": "integer", "description": "Line amount in euros."} in (
        columns
    )
    assert body["row_policy"] is None
    assert body["examples"][0] == "Monthly revenue trend this year and the top 3 drivers"
    assert "Own accounts only" in dan["row_policy"]
    assert "customers they own" in dan["row_policy"]


async def test_people_without_access_see_why(api: TestClient, acme: Acme) -> None:
    for who in ("bob", "mallory", "erin"):
        response = api.get("/api/v1/analytics/model", headers=acme.as_(who))

        assert response.status_code == 200
        assert response.json() == {
            "available": False,
            "reason": "no_access",
            "message": "You do not have access to the analytics data.",
        }


# --- Asking -----------------------------------------------------------------------------------


async def test_an_answer_is_stored_for_the_asker_only(api: TestClient, acme: Acme) -> None:
    use_model(
        api,
        Composed(
            narrative="Revenue from January to September 2026 was €38.9M [Q2].",
            chart=ChartDraft(
                type="line",
                query="Q1",
                x="month",
                y=["revenue"],
                series=None,
                title="Monthly revenue",
                y_format="eur",
            ),
            main_query="Q1",
        ),
        turns=[
            [
                ("query_metrics", {"metrics": ["revenue"], "dimensions": ["month"]}),
                ("query_metrics", {"metrics": ["revenue"], "purpose": "Total revenue"}),
            ]
        ],
    )

    answer = ask(api, acme, "carol", "Monthly revenue trend this year?")

    assert set(answer) == {
        "id",
        "question",
        "status",
        "error",
        "narrative",
        "chart",
        "queries",
        "main_query",
        "verification",
        "sources",
        "usage",
        "created_at",
    }
    assert answer["status"] == "answered"
    assert answer["chart"]["type"] == "line"
    assert answer["main_query"] == "Q1"
    assert len(answer["queries"][0]["rows"]) == 9  # January to September
    assert answer["queries"][1]["rows"] == [[38_900_000]]
    assert set(answer["queries"][0]) == {
        "id",
        "purpose",
        "sql",
        "columns",
        "rows",
        "row_count",
        "truncated",
        "elapsed_ms",
        "error",
        "dataset",
        "dialect",
    }
    assert (answer["queries"][0]["dataset"], answer["queries"][0]["dialect"]) == (
        "uploads",
        "duckdb",
    )
    assert answer["queries"][0]["columns"][0] == {"name": "month", "type": "DATE", "format": "date"}
    assert answer["verification"] == {
        "numbers": [{"text": "€38.9M", "value": 38_900_000.0, "verified": True, "query": "Q2"}],
        "verified": 1,
        "total": 1,
    }
    assert answer["sources"] == [
        {
            "table": "invoice_lines",
            "description": "One row per invoice line. Revenue counts lines whose status is not "
            "'void' (void lines were cancelled and re-issued).",
            "dataset": "uploads",
        }
    ]
    assert set(answer["usage"]) == {"model", "input_tokens", "output_tokens", "cost_usd"}

    runs = api.get("/api/v1/analytics/runs", headers=acme.as_("carol")).json()
    again = api.get(f"/api/v1/analytics/runs/{answer['id']}", headers=acme.as_("carol"))
    dans = api.get("/api/v1/analytics/runs", headers=acme.as_("dan")).json()
    not_dans = api.get(f"/api/v1/analytics/runs/{answer['id']}", headers=acme.as_("dan"))
    missing = api.get(f"/api/v1/analytics/runs/{uuid.uuid4()}", headers=acme.as_("carol"))

    assert runs == [
        {
            "id": answer["id"],
            "question": "Monthly revenue trend this year?",
            "status": "answered",
            "verified": 1,
            "total": 1,
            "created_at": answer["created_at"],
        }
    ]
    assert again.status_code == 200
    assert again.json() == answer
    assert dans == []
    assert (not_dans.status_code, not_dans.json()["code"]) == (404, "not_found")
    assert missing.status_code == 404


async def test_dans_row_policy_applies_through_ask_and_query(api: TestClient, acme: Acme) -> None:
    use_model(
        api,
        Composed(narrative="You see your own customers [Q1].", chart=None, main_query="Q1"),
        turns=[
            [
                ("run_sql", {"sql": "SELECT DISTINCT account_owner FROM customers"}),
                ("query_metrics", {"metrics": ["revenue"]}),
                ("run_sql", {"sql": "SELECT count(*) AS lines FROM invoice_lines"}),
            ]
        ],
    )

    answer = ask(api, acme, "dan", "Whose customers do I see, and their revenue?")

    owners, revenue, lines = answer["queries"]
    assert owners["rows"] == [["dan@acme.test"]]
    assert 0 < revenue["rows"][0][0] < 38_900_000
    assert lines["rows"] == [[DAN_LINES]]

    # Re-running the answer's SQL applies the policy of whoever runs it.
    as_dan = query(api, acme, "dan", owners["sql"])
    as_diego = query(api, acme, "diego.alvarez", owners["sql"])
    dan_lines = query(api, acme, "dan", "SELECT count(*) FROM invoice_lines")
    sneaky = query(
        api, acme, "dan", "SELECT count(*) FROM invoice_lines WHERE customer_id = 'C001'"
    )

    assert as_dan.status_code == 200, as_dan.text
    assert set(as_dan.json()) == {"columns", "rows", "row_count", "truncated", "elapsed_ms"}
    assert as_dan.json()["rows"] == [["dan@acme.test"]]
    assert sorted(r[0] for r in as_diego.json()["rows"]) == [
        "dan@acme.test",
        "diego.alvarez@acme.test",
    ]
    assert dan_lines.json()["rows"] == [[DAN_LINES]]
    assert sneaky.json()["rows"] == [[0]]  # Bluefin (C001) is not Dan's


async def test_bob_and_mallory_get_no_access_and_no_model_call(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    llm = use_model(api, turns=[])

    bob = ask(api, acme, "bob", "What was revenue in September?")
    mallory = ask(api, acme, "mallory", "Show me every customer's revenue.")
    bob_query = query(api, acme, "bob", "SELECT count(*) FROM customers")
    mallory_query = query(api, acme, "mallory", "SELECT * FROM customers")

    assert (bob["status"], mallory["status"]) == ("no_access", "no_access")
    assert bob["queries"] == mallory["queries"] == []
    assert llm.requests == []
    for refused in (bob_query, mallory_query):
        assert refused.status_code == 403
        assert refused.json()["code"] == "no_access"
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        denied = (
            await session.scalars(
                select(AuditEvent.details["kind"].astext).where(
                    AuditEvent.action == "analytics.query",
                    AuditEvent.details["status"].astext == "denied",
                )
            )
        ).all()
    assert sorted(denied) == ["ask", "ask", "sql", "sql"]
    runs = api.get("/api/v1/analytics/runs", headers=acme.as_("bob")).json()
    assert [r["status"] for r in runs] == ["no_access"]


async def test_query_rejects_unsafe_or_broken_sql(api: TestClient, acme: Acme) -> None:
    dropped = query(api, acme, "carol", "DROP TABLE customers")
    files = query(api, acme, "carol", "SELECT * FROM read_csv('/etc/passwd')")
    broken = query(api, acme, "carol", "SELECT no_such_column FROM customers")
    capped = query(api, acme, "carol", "SELECT * FROM invoice_lines")

    for response in (dropped, files, broken):
        assert response.status_code == 422, response.text
        assert response.json()["code"] == "sql_rejected"
        assert response.json()["detail"]
    assert "read_csv" in files.json()["detail"]
    assert capped.status_code == 200
    assert (capped.json()["row_count"], capped.json()["truncated"]) == (478, False)


async def test_a_question_must_have_a_length(api: TestClient, acme: Acme) -> None:
    empty = api.post(
        "/api/v1/analytics/ask", json={"question": ""}, headers=acme.as_("carol", write=True)
    )
    long = api.post(
        "/api/v1/analytics/ask",
        json={"question": "x" * 1001},
        headers=acme.as_("carol", write=True),
    )
    blank = api.post(
        "/api/v1/analytics/ask", json={"question": "   "}, headers=acme.as_("carol", write=True)
    )

    assert (empty.status_code, long.status_code) == (422, 422)
    assert (blank.status_code, blank.json()["code"]) == (422, "invalid_question")


# --- Administration -------------------------------------------------------------------------


async def test_admins_see_the_model_and_the_data(api: TestClient, acme: Acme) -> None:
    erin = api.get("/api/v1/admin/analytics", headers=acme.as_("erin"))
    carol = api.get("/api/v1/admin/analytics", headers=acme.as_("carol"))

    assert erin.status_code == 200, erin.text
    body = erin.json()
    assert body["yaml"] == (ACME_ANALYTICS / "semantic.yaml").read_text()
    assert body["dataset"]["version"]
    tables = {t["name"]: t for t in body["dataset"]["tables"]}
    assert {name: t["rows"] for name, t in tables.items()} == {
        "customers": 24,
        "invoice_lines": 478,
        "opportunities": 21,
        "products": 4,
    }
    assert {"name": "amount_eur", "type": "BIGINT"} in tables["invoice_lines"]["columns"]
    assert (carol.status_code, carol.json()["code"]) == (403, "forbidden")


async def test_a_bad_model_is_refused_with_the_problems(api: TestClient, acme: Acme) -> None:
    original = (ACME_ANALYTICS / "semantic.yaml").read_text()

    def put(yaml: str, who: str = "erin") -> Any:
        return api.put(
            "/api/v1/admin/analytics/model", json={"yaml": yaml}, headers=acme.as_(who, write=True)
        )

    not_yaml = put("tables: [")
    unknown_group = put(original.replace("group:leadership", "group:board"))
    bad_metric = put(original.replace("sql: sum(quantity)", "sql: quantity"))
    by_carol = put(original, "carol")
    renamed = put(original.replace("Acme Robotics finance and sales", "Acme revenue"))

    for refused in (not_yaml, unknown_group, bad_metric):
        assert refused.status_code == 422
        assert refused.json()["code"] == "invalid_model"
    assert "group:board" in unknown_group.json()["detail"]
    assert "must aggregate" in bad_metric.json()["detail"]
    assert (by_carol.status_code, by_carol.json()["code"]) == (403, "forbidden")
    assert renamed.status_code == 200, renamed.text
    assert "name: Acme revenue" in renamed.json()["yaml"]
    model = api.get("/api/v1/analytics/model", headers=acme.as_("carol")).json()
    assert model["name"] == "Acme revenue"


async def test_uploading_a_table_creates_a_new_dataset_version(api: TestClient, acme: Acme) -> None:
    before = api.get("/api/v1/admin/analytics", headers=acme.as_("erin")).json()
    headers = {**acme.as_("erin", write=True), "Content-Type": "text/csv"}

    added = api.post(
        "/api/v1/admin/analytics/tables",
        params={"name": "targets"},
        content=TARGETS,
        headers=headers,
    )

    assert added.status_code == 200, added.text
    after = added.json()
    assert after["dataset"]["version"] != before["dataset"]["version"]
    targets = next(t for t in after["dataset"]["tables"] if t["name"] == "targets")
    assert targets["rows"] == 3
    assert [c["name"] for c in targets["columns"]] == ["month", "target_eur"]
    # The other tables came along, and queries keep working on the new version.
    assert len(after["dataset"]["tables"]) == 5
    assert query(api, acme, "carol", "SELECT count(*) FROM customers").json()["rows"] == [[24]]

    removed = api.delete(
        "/api/v1/admin/analytics/tables/targets", headers=acme.as_("erin", write=True)
    )
    again = api.delete(
        "/api/v1/admin/analytics/tables/targets", headers=acme.as_("erin", write=True)
    )

    assert removed.status_code == 200, removed.text
    assert removed.json()["dataset"]["version"] not in (
        before["dataset"]["version"],
        after["dataset"]["version"],
    )
    assert "targets" not in {t["name"] for t in removed.json()["dataset"]["tables"]}
    assert (again.status_code, again.json()["code"]) == (404, "not_found")


async def test_uploads_are_checked(api: TestClient, acme: Acme) -> None:
    def upload(name: str, data: bytes, content_type: str = "text/csv", who: str = "erin") -> Any:
        return api.post(
            "/api/v1/admin/analytics/tables",
            params={"name": name},
            content=data,
            headers={**acme.as_(who, write=True), "Content-Type": content_type},
        )

    too_large = upload("big", b"a,b\n" + b"1,2\n" * (MAX_UPLOAD // 4 + 1))
    bad_name = upload("Bad-Name", TARGETS)
    reserved = upload("pg_targets", TARGETS)
    not_csv = upload("targets", b"%PDF-1.7 not a table", "application/pdf")
    empty = upload("targets", b"")
    by_carol = upload("targets", TARGETS, who="carol")

    assert (too_large.status_code, too_large.json()["code"]) == (413, "too_large")
    for refused in (bad_name, reserved, not_csv, empty):
        assert refused.status_code == 422, refused.text
        assert refused.json()["code"] == "invalid_table"
        assert refused.json()["detail"]
    assert (by_carol.status_code, by_carol.json()["code"]) == (403, "forbidden")
    tables = api.get("/api/v1/admin/analytics", headers=acme.as_("erin")).json()["dataset"]
    assert len(tables["tables"]) == 4  # nothing was added
