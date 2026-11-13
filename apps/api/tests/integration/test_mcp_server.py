"""The NeedWise MCP server over real HTTP (JSON-RPC, as MCP clients send it), with both
permission locks. Embeddings are the hash provider; the language model is a scripted fake.

Needs TEST_QDRANT_URL, TEST_VALKEY_URL and TEST_OPENFGA_URL (+ TEST_OPENFGA_API_KEY).
"""

import asyncio
import json
import os
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from needwise.core.config import Settings
from needwise.core.tenancy import tenant_transaction
from needwise.main import create_app
from needwise.modules.authz.openfga import OpenFgaClient
from needwise.modules.authz.sync import ensure_store, reconcile
from needwise.modules.governance.models import AuditEvent
from needwise.modules.knowledge.models import Collection
from needwise.modules.retrieval.qdrant import QdrantClient
from tests.fakes import FakeLLM
from tests.integration.conftest import VALKEY_URL, Acme, Database, SessionFactory, seed_acme

QDRANT_URL = os.environ.get("TEST_QDRANT_URL")
OPENFGA_URL = os.environ.get("TEST_OPENFGA_URL")
OPENFGA_KEY = os.environ.get("TEST_OPENFGA_API_KEY")
pytestmark = pytest.mark.skipif(
    not (QDRANT_URL and VALKEY_URL and OPENFGA_URL),
    reason="set TEST_QDRANT_URL, TEST_VALKEY_URL and TEST_OPENFGA_URL",
)
LEAVE = b"# Leave Policy\n\n## Parental leave\nParental leave is 16 weeks, fully paid.\n"
PAYROLL = b"# Payroll Q3\n\n## Totals\nQ3 payroll was 6.1 million euros. CANARY-PAYROLL-2K8\n"
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def model_name() -> str:
    return f"test_{uuid.uuid4().hex[:10]}"


@pytest.fixture(scope="module", autouse=True)
def drop_test_collection(model_name: str) -> Iterator[None]:
    yield

    async def drop() -> None:
        async with httpx.AsyncClient() as http:
            client = QdrantClient(http, QDRANT_URL or "")
            if await client.collection_exists(f"chunks_{model_name}_64"):
                await client.delete_collection(f"chunks_{model_name}_64")

    asyncio.run(drop())


@pytest.fixture
async def synced(owner_sessions: SessionFactory) -> AsyncIterator[Acme]:
    async with httpx.AsyncClient(timeout=15) as http:
        fga = OpenFgaClient(http, OPENFGA_URL or "", OPENFGA_KEY)
        acme = await seed_acme(owner_sessions)
        store = await ensure_store(owner_sessions, fga, acme.tenant_id)
        await reconcile(owner_sessions, fga, acme.tenant_id, store)
        yield acme
        await fga.delete_store(store)


@pytest.fixture
def api(database: Database, tmp_path: Path, model_name: str) -> Iterator[TestClient]:
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        app_env="test",
        database_url=database.app_url,
        valkey_url=VALKEY_URL or "",
        qdrant_url=QDRANT_URL or "",
        openfga_api_url=OPENFGA_URL or "",
        openfga_api_key=OPENFGA_KEY or "",
        authz_outbox_worker=False,
        ingestion_mode="inline",
        embedding_provider="hash",
        embedding_model=model_name,
        embedding_dimensions=64,
        retrieval_min_similarity=0.0,  # the floor is calibrated for real embeddings
        object_storage_path=str(tmp_path),
    )
    with TestClient(create_app(settings)) as client:
        yield client


def _upload(
    api: TestClient, acme: Acme, who: str, collection: uuid.UUID, name: str, data: bytes
) -> None:
    document = api.post(
        f"/api/v1/collections/{collection}/documents",
        params={"filename": name},
        content=data,
        headers={**acme.as_(who, write=True), "Content-Type": "text/markdown"},
    ).json()["document"]["id"]
    deadline = time.monotonic() + 20
    while (
        api.get(f"/api/v1/documents/{document}", headers=acme.as_(who)).json()["status"] != "ready"
    ):
        assert time.monotonic() < deadline
        time.sleep(0.2)


@pytest.fixture
async def acme(api: TestClient, synced: Acme, app_sessions: SessionFactory) -> Acme:
    async with tenant_transaction(app_sessions, synced.tenant_id) as session:
        keys = {c.key: c.id for c in await session.scalars(select(Collection))}
    _upload(api, synced, "alice", keys["company-handbook"], "leave.md", LEAVE)
    _upload(api, synced, "hannah.schmidt", keys["finance-payroll"], "payroll.md", PAYROLL)
    return synced


def token(api: TestClient, acme: Acme, who: str, scopes: list[str]) -> str:
    response = api.post(
        "/api/v1/me/tokens",
        json={"name": f"mcp-{who}", "scopes": scopes},
        headers=acme.as_(who, write=True),
    )
    assert response.status_code == 201, response.text
    return response.json()["token"]


def rpc(
    api: TestClient, bearer: str | None, method: str, params: dict[str, Any] | None = None
) -> Any:
    headers = {**MCP_HEADERS, **({"Authorization": f"Bearer {bearer}"} if bearer else {})}
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    return api.post("/mcp", content=json.dumps(body), headers=headers)


def call(api: TestClient, bearer: str, tool: str, **arguments: Any) -> tuple[str, bool]:
    response = rpc(api, bearer, "tools/call", {"name": tool, "arguments": arguments})
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    return "\n".join(c.get("text", "") for c in result["content"]), bool(result.get("isError"))


async def test_no_token_means_no_mcp(api: TestClient, acme: Acme) -> None:
    missing = rpc(api, None, "tools/list")
    forged = rpc(api, "nw_pat_acme_not-a-real-token", "tools/list")

    assert missing.status_code == 401
    assert missing.headers["www-authenticate"].startswith("Bearer")
    assert forged.status_code == 401


async def test_tools_are_listed_for_a_valid_token(api: TestClient, acme: Acme) -> None:
    bearer = token(api, acme, "bob", ["knowledge:read"])

    listed = rpc(api, bearer, "tools/list")

    assert listed.status_code == 200, listed.text
    names = {t["name"] for t in listed.json()["result"]["tools"]}
    assert {"ask", "search_knowledge", "list_collections"} <= names


async def test_search_through_mcp_respects_each_persons_permissions(
    api: TestClient, acme: Acme
) -> None:
    bob = token(api, acme, "bob", ["knowledge:read"])
    hannah = token(api, acme, "hannah.schmidt", ["knowledge:read"])

    bob_leave, _ = call(api, bob, "search_knowledge", query="parental leave weeks")
    bob_payroll, _ = call(api, bob, "search_knowledge", query="Q3 payroll total")
    hannah_payroll, _ = call(api, hannah, "search_knowledge", query="Q3 payroll total")

    assert "16 weeks" in bob_leave
    assert "CANARY-PAYROLL-2K8" not in bob_payroll  # lock 1 and lock 2 apply through MCP too
    assert "CANARY-PAYROLL-2K8" in hannah_payroll


async def test_token_scopes_limit_what_mcp_can_do(api: TestClient, acme: Acme) -> None:
    read_only = token(api, acme, "bob", ["knowledge:read"])

    text, is_error = call(api, read_only, "ask", question="How long is parental leave?")
    collections, _ = call(api, read_only, "list_collections")

    assert is_error
    assert "assistant:ask" in text
    assert "Company Handbook" in collections
    assert "HR Employee Records" not in collections


async def test_every_mcp_call_is_audited_ask_and_list_collections_too(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    # Only the registry tools (search_knowledge, the analytics tools) wrote audit events: an
    # answer drawn from restricted documents over MCP left no trail, unlike POST /ask.
    app: Any = api.app
    app.state.resources.llm = FakeLLM("Q3 payroll was 6.1 million euros [S1].")
    hannah = token(api, acme, "hannah.schmidt", ["knowledge:read", "assistant:ask"])

    answered, is_error = call(api, hannah, "ask", question="What was the Q3 payroll total?")
    listed, _ = call(api, hannah, "list_collections")

    assert not is_error, answered
    assert "Finance" in listed
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        events = (
            await session.scalars(
                select(AuditEvent)
                .where(AuditEvent.action.in_(["assistant.answered", "tool.called"]))
                .order_by(AuditEvent.seq)
            )
        ).all()
    answers = [e for e in events if e.action == "assistant.answered"]
    assert len(answers) == 1
    assert answers[0].actor_id == acme.users["hannah.schmidt"]
    assert answers[0].details["via"] == "mcp"
    assert answers[0].details["documents"]  # which documents it drew on, never the text
    assert "6.1" not in json.dumps(answers[0].details)
    assert [(e.resource_id, e.details["caller"]) for e in events if e.action == "tool.called"] == [
        ("list_collections", "mcp")
    ]
