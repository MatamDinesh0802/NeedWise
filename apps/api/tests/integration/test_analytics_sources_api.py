"""SQL sources and the answer's live progress over HTTP (docs/analytics-sql-sources.md), on a
seeded Acme tenant with the uploaded dataset and the throwaway PostgreSQL shop database:
admins connect and model a source (the password never comes back), people see it as a second
dataset and query it with their row policies, and an answer streams its steps (planning, the
query compiled and run, verifying, charting) before it is stored."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.analytics_support import ACME_ANALYTICS
from tests.fakes import FakeLLM, ToolTurn
from tests.integration.conftest import VALKEY_URL, Acme, Database, SessionFactory, seed_acme
from tests.integration.shop_databases import SHOP_YAML, ShopServer
from workmind.core.config import Settings
from workmind.main import create_app
from workmind.modules.analytics.agent import ChartDraft, Composed
from workmind.seed.analytics_load import seed_analytics

pytestmark = pytest.mark.skipif(not VALKEY_URL, reason="set TEST_VALKEY_URL")
TOTAL = "SELECT sum(amount_eur) AS shop_revenue FROM orders WHERE status <> 'refunded'"
BY_REGION = (
    "SELECT c.region, sum(o.amount_eur) AS shop_revenue FROM orders o JOIN customers c "
    "ON c.customer_id = o.customer_id WHERE o.status <> 'refunded' GROUP BY c.region "
    "ORDER BY shop_revenue DESC"
)


@pytest.fixture
def api(database: Database, tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        app_env="test",
        database_url=database.app_url,
        valkey_url=VALKEY_URL or "",
        authz_outbox_worker=False,
        object_storage_path=str(tmp_path),
        analytics_source_private_networks="127.0.0.0/8,::1/128",
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


def connect(api: TestClient, acme: Acme, server: ShopServer) -> dict[str, Any]:
    erin = acme.as_("erin", write=True)
    created = api.post(
        "/api/v1/admin/analytics/sources",
        json={
            "key": "shop",
            "name": "Shop orders",
            "kind": server.kind,
            "host": server.host,
            "port": server.port,
            "database": server.database,
            "username": server.reader,
            "password": server.password,
            "tls_mode": "disable",
            "schemas": ["shop"],
        },
        headers=erin,
    )
    assert created.status_code == 201, created.text
    model = api.put(
        "/api/v1/admin/analytics/sources/shop/model",
        json={"yaml": SHOP_YAML.read_text()},
        headers=erin,
    )
    assert model.status_code == 200, model.text
    return model.json()


def events(api: TestClient, acme: Acme, who: str, run: str) -> list[tuple[str, dict[str, Any]]]:
    received: list[tuple[str, dict[str, Any]]] = []
    name = ""
    with api.stream(
        "GET", f"/api/v1/analytics/runs/{run}/events", headers=acme.as_(who)
    ) as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                received.append((name, json.loads(line[6:])))
                if name in ("run.completed", "run.failed"):
                    break
    return received


def use_model(api: TestClient, *composed: Composed, turns: list[ToolTurn]) -> FakeLLM:
    llm = FakeLLM(structured=list(composed), tool_turns=turns)
    app: Any = api.app
    app.state.resources.llm = llm
    return llm


async def test_admins_connect_and_model_a_source_over_http(
    api: TestClient, acme: Acme, shop_postgres: ShopServer
) -> None:
    erin = acme.as_("erin", write=True)
    denied = api.get("/api/v1/admin/analytics/sources", headers=acme.as_("carol"))
    assert denied.status_code == 403

    source = connect(api, acme, shop_postgres)
    listed = api.get("/api/v1/admin/analytics/sources", headers=erin)
    tested = api.post("/api/v1/admin/analytics/sources/shop/test", headers=erin)
    catalog = api.get("/api/v1/admin/analytics/sources/shop/catalog", headers=erin)
    moved = api.patch(
        "/api/v1/admin/analytics/sources/shop", json={"host": "localhost"}, headers=erin
    )
    metadata = api.patch(
        "/api/v1/admin/analytics/sources/shop",
        json={"host": "169.254.169.254", "password": "x"},
        headers=erin,
    )

    assert source["password_set"] is True
    assert "password" not in source
    assert listed.status_code == 200
    assert shop_postgres.password not in listed.text
    assert [s["key"] for s in listed.json()] == ["shop"]
    assert tested.json()["ok"] is True, tested.text
    assert "PostgreSQL" in tested.json()["server_version"]
    tables = {(t["schema"], t["name"]) for t in catalog.json()["tables"]}
    assert ("shop", "orders") in tables
    assert not any(schema == "hr" for schema, _ in tables)
    assert (moved.status_code, moved.json()["code"]) == (422, "password_required")
    assert (metadata.status_code, metadata.json()["code"]) == (422, "invalid_source")
    bad = api.put(
        "/api/v1/admin/analytics/sources/shop/model",
        json={"yaml": SHOP_YAML.read_text().replace("shop.orders", "shop.nope")},
        headers=erin,
    )
    assert (bad.status_code, bad.json()["code"]) == (422, "invalid_model")
    missing = api.post("/api/v1/admin/analytics/sources/nope/test", headers=erin)
    assert missing.status_code == 404
    deleted = api.delete("/api/v1/admin/analytics/sources/shop", headers=erin)
    assert deleted.status_code == 204
    assert api.get("/api/v1/admin/analytics/sources", headers=erin).json() == []


async def test_people_see_the_source_as_a_dataset_with_their_rows(
    api: TestClient, acme: Acme, shop_postgres: ShopServer
) -> None:
    connect(api, acme, shop_postgres)

    carol = api.get("/api/v1/analytics/model", headers=acme.as_("carol")).json()
    dan = api.get("/api/v1/analytics/model", headers=acme.as_("dan")).json()
    everyone = api.post(
        "/api/v1/analytics/query",
        json={"sql": "SELECT count(*) AS n FROM customers", "dataset": "shop"},
        headers=acme.as_("carol", write=True),
    )
    his = api.post(
        "/api/v1/analytics/query",
        json={"sql": "SELECT count(*) AS n FROM customers", "dataset": "shop"},
        headers=acme.as_("dan", write=True),
    )
    files = api.post(
        "/api/v1/analytics/query",
        json={"sql": "SELECT count(*) AS n FROM customers"},
        headers=acme.as_("carol", write=True),
    )
    attack = api.post(
        "/api/v1/analytics/query",
        json={"sql": "SELECT * FROM hr.salaries", "dataset": "shop"},
        headers=acme.as_("carol", write=True),
    )
    unknown = api.post(
        "/api/v1/analytics/query",
        json={"sql": "SELECT 1", "dataset": "nope"},
        headers=acme.as_("carol", write=True),
    )

    assert carol["name"] == "Acme Robotics finance and sales"  # the uploaded files come first
    assert [(d["key"], d["kind"], d["dialect"]) for d in carol["datasets"]] == [
        ("uploads", "files", "duckdb"),
        ("shop", "postgres", "postgres"),
    ]
    shop = carol["datasets"][1]
    assert shop["name"] == "Shop orders"
    assert {m["name"] for m in shop["metrics"]} >= {"shop_revenue", "orders_count"}
    assert shop["row_policy"] is None
    assert "Own shop accounts only" in dan["datasets"][1]["row_policy"]
    assert everyone.json()["rows"] == [[10]]
    assert his.json()["rows"] == [[4]]
    assert files.json()["rows"] == [[24]]
    assert (attack.status_code, attack.json()["code"]) == (422, "sql_rejected")
    assert unknown.status_code == 404
    bob = api.get("/api/v1/analytics/model", headers=acme.as_("bob")).json()
    assert (bob["available"], bob["reason"]) == (False, "no_access")


async def test_an_answer_streams_its_steps_then_is_stored(
    api: TestClient, acme: Acme, shop_postgres: ShopServer
) -> None:
    connect(api, acme, shop_postgres)
    use_model(
        api,
        Composed(
            narrative="Shop revenue was €870,066.75 [Q1]; West led with €279,080.10 [Q2].",
            chart=ChartDraft(
                type="bar",
                query="Q2",
                x="region",
                y=["shop_revenue"],
                series=None,
                title="Shop revenue by region",
                y_format="eur",
            ),
            main_query="Q1",
        ),
        turns=[
            [("run_sql", {"sql": "SELECT nope FROM orders", "dataset": "shop"})],
            [("run_sql", {"sql": TOTAL, "dataset": "shop", "purpose": "Shop revenue"})],
            [("run_sql", {"sql": BY_REGION, "dataset": "shop", "purpose": "By region"})],
        ],
    )

    started = api.post(
        "/api/v1/analytics/runs",
        json={"question": "What was shop revenue, and which region led?"},
        headers=acme.as_("carol", write=True),
    )
    assert started.status_code == 202, started.text
    run_id = started.json()["run_id"]
    received = events(api, acme, "carol", run_id)
    others = api.get(f"/api/v1/analytics/runs/{run_id}/events", headers=acme.as_("dan"))
    stored = api.get(f"/api/v1/analytics/runs/{run_id}", headers=acme.as_("carol"))

    names = [name for name, _ in received]
    assert names[0] == "run.started"
    assert names[-1] == "run.completed"
    steps = [data["step"] for name, data in received if name == "analytics.step"]
    assert steps == [
        "planning",
        "query_compiled",
        "executing",
        "query_failed",
        "query_compiled",
        "executing",
        "query_done",
        "query_compiled",
        "executing",
        "query_done",
        "composing",
        "verifying",
        "charting",
    ]
    by_step = {data["step"]: data for name, data in received if name == "analytics.step"}
    assert by_step["planning"]["datasets"] == ["uploads", "shop"]
    assert (by_step["query_compiled"]["dataset"], by_step["query_compiled"]["dialect"]) == (
        "shop",
        "postgres",
    )
    assert '"shop"' not in by_step["query_compiled"]["sql"]  # model names, not the database's
    assert 'column "nope" does not exist' in by_step["query_failed"]["error"]
    assert by_step["verifying"] == {"step": "verifying", "verified": 2, "total": 2}
    assert by_step["charting"]["type"] == "bar"
    assert received[-1][1]["status"] == "answered"
    assert others.status_code == 404  # someone else's run
    answer = stored.json()
    assert [(q["id"], q["dataset"], q["dialect"]) for q in answer["queries"]] == [
        ("Q1", "shop", "postgres"),
        ("Q2", "shop", "postgres"),
    ]
    assert answer["queries"][0]["rows"] == [[870066.75]]
    assert {(s["dataset"], s["table"]) for s in answer["sources"]} == {
        ("shop", "orders"),
        ("shop", "customers"),
    }
    rerun = api.post(
        "/api/v1/analytics/query",
        json={"sql": answer["queries"][1]["sql"], "dataset": "shop"},
        headers=acme.as_("carol", write=True),
    )
    assert rerun.json()["rows"] == answer["queries"][1]["rows"]


async def test_a_streamed_question_from_someone_without_access_stops_before_the_model(
    api: TestClient, acme: Acme, shop_postgres: ShopServer
) -> None:
    connect(api, acme, shop_postgres)
    llm = use_model(api, turns=[])

    started = api.post(
        "/api/v1/analytics/runs",
        json={"question": "Shop revenue?"},
        headers=acme.as_("bob", write=True),
    )
    received = events(api, acme, "bob", started.json()["run_id"])

    assert [name for name, _ in received] == ["run.started", "run.completed"]
    assert received[-1][1]["status"] == "no_access"
    assert llm.requests == []
