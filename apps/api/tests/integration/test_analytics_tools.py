"""query_data and describe_data in research mode and over MCP, on a seeded Acme tenant: offered
only to people with access to the data, and run as the person asking (their row policies)."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from needwise.core.config import Settings
from needwise.main import create_app
from needwise.seed.analytics_load import seed_analytics
from tests.analytics_support import ACME_ANALYTICS
from tests.fakes import FakeLLM, ToolTurn
from tests.integration.conftest import VALKEY_URL, Acme, Database, SessionFactory, seed_acme

pytestmark = pytest.mark.skipif(not VALKEY_URL, reason="set TEST_VALKEY_URL")
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
BY_OWNER = {"metrics": ["revenue"], "dimensions": ["account_owner"]}
ANALYTICS_TOOLS = {"describe_data", "query_data"}


@pytest.fixture
def api(database: Database, tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        app_env="test",
        database_url=database.app_url,
        valkey_url=VALKEY_URL or "",
        authz_outbox_worker=False,
        object_storage_path=str(tmp_path),
    )
    with TestClient(create_app(settings)) as client:
        yield client


@pytest.fixture
async def acme(owner_sessions: SessionFactory, tmp_path: Path) -> Acme:
    seeded = await seed_acme(owner_sessions)
    assert await seed_analytics(
        owner_sessions, seeded.tenant_id, ACME_ANALYTICS, tmp_path / "analytics"
    )
    return seeded


def research(api: TestClient, acme: Acme, who: str, turns: list[ToolTurn]) -> FakeLLM:
    llm = FakeLLM(tool_turns=turns, tool_text="Here is what the data says.")
    app: Any = api.app
    app.state.resources.llm = llm
    response = api.post(
        "/api/v1/research",
        json={"question": "What is my revenue by account owner?"},
        headers=acme.as_(who, write=True),
    )
    assert response.status_code == 200, response.text
    return llm


async def test_research_offers_query_data_to_people_with_access(
    api: TestClient, acme: Acme
) -> None:
    dan = research(api, acme, "dan", [[("describe_data", {}), ("query_data", BY_OWNER)]])
    carol = research(api, acme, "carol", [[("query_data", BY_OWNER)]])

    assert {t.name for t in dan.offered} >= ANALYTICS_TOOLS
    described, queried = dan.tool_replies
    assert not described.is_error
    assert "Own accounts only" in described.content
    assert not queried.is_error
    assert "dan@acme.test" in queried.content
    assert "diego.alvarez@acme.test" not in queried.content  # Dan's row policy applies
    assert "diego.alvarez@acme.test" in carol.tool_replies[0].content


async def test_research_hides_query_data_from_people_without_access(
    api: TestClient, acme: Acme
) -> None:
    bob = research(api, acme, "bob", [[("query_data", BY_OWNER)]])

    assert not ANALYTICS_TOOLS & {t.name for t in bob.offered}
    # A model that calls it anyway (by name) is refused by the service, as Bob.
    (reply,) = bob.tool_replies
    assert reply.is_error
    assert reply.content.startswith("analytics_forbidden:")


# --- MCP --------------------------------------------------------------------------------------


def token(api: TestClient, acme: Acme, who: str, scopes: list[str]) -> str:
    response = api.post(
        "/api/v1/me/tokens",
        json={"name": f"mcp-{who}", "scopes": scopes},
        headers=acme.as_(who, write=True),
    )
    assert response.status_code == 201, response.text
    return response.json()["token"]


def rpc(api: TestClient, bearer: str, method: str, params: dict[str, Any] | None = None) -> Any:
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    response = api.post(
        "/mcp",
        content=json.dumps(body),
        headers={**MCP_HEADERS, "Authorization": f"Bearer {bearer}"},
    )
    assert response.status_code == 200, response.text
    return response.json()["result"]


def listed(api: TestClient, bearer: str) -> set[str]:
    return {t["name"] for t in rpc(api, bearer, "tools/list")["tools"]}


def call(api: TestClient, bearer: str, tool: str, **arguments: Any) -> tuple[str, bool]:
    result = rpc(api, bearer, "tools/call", {"name": tool, "arguments": arguments})
    return "\n".join(c.get("text", "") for c in result["content"]), bool(result.get("isError"))


async def test_mcp_lists_the_analytics_tools_only_to_people_with_access(
    api: TestClient, acme: Acme
) -> None:
    carol = token(api, acme, "carol", ["assistant:ask"])
    carol_read_only = token(api, acme, "carol", ["knowledge:read"])
    bob = token(api, acme, "bob", ["assistant:ask", "knowledge:read"])

    assert listed(api, carol) >= ANALYTICS_TOOLS
    assert {"ask", "search_knowledge", "list_collections"} <= listed(api, bob)
    assert not ANALYTICS_TOOLS & listed(api, bob)
    assert not ANALYTICS_TOOLS & listed(api, carol_read_only)  # the token lacks the scope


async def test_mcp_queries_run_as_the_tokens_owner(api: TestClient, acme: Acme) -> None:
    dan = token(api, acme, "dan", ["assistant:ask"])
    carol = token(api, acme, "carol", ["assistant:ask"])
    bob = token(api, acme, "bob", ["assistant:ask"])
    read_only = token(api, acme, "carol", ["knowledge:read"])

    dans, dan_error = call(api, dan, "query_data", **BY_OWNER)
    carols, _ = call(api, carol, "query_data", **BY_OWNER)
    september, _ = call(
        api,
        carol,
        "query_data",
        metrics=["revenue"],
        filters=[{"dimension": "product_line", "op": "=", "values": ["FleetOS"]}],
        date_from="2026-09-01",
        date_to="2026-09-30",
    )
    described, _ = call(api, carol, "describe_data")
    bobs, bob_error = call(api, bob, "query_data", **BY_OWNER)
    scoped, scope_error = call(api, read_only, "query_data", **BY_OWNER)

    assert not dan_error
    assert "dan@acme.test" in dans
    assert "diego.alvarez@acme.test" not in dans
    assert "diego.alvarez@acme.test" in carols
    assert "1520000" in september
    assert "revenue [eur, invoice_lines]" in described
    assert bob_error
    assert "access" in bobs
    assert scope_error
    assert "assistant:ask" in scoped
