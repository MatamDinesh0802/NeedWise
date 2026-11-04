"""Search and ask over real HTTP with both permission locks: Qdrant ACL filters (lock 1) and
OpenFGA rechecks (lock 2). The language model is a fake; embeddings are the hash provider.

Needs TEST_QDRANT_URL, TEST_VALKEY_URL and TEST_OPENFGA_URL (+ TEST_OPENFGA_API_KEY).
"""

import asyncio
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

from tests.integration.conftest import VALKEY_URL, Acme, Database, SessionFactory, seed_acme
from workmind.core.config import Settings
from workmind.core.tenancy import tenant_transaction
from workmind.main import create_app
from workmind.modules.authz.openfga import OpenFgaClient, RelationTuple
from workmind.modules.authz.sync import ensure_store, reconcile
from workmind.modules.knowledge.models import Collection
from workmind.modules.retrieval.qdrant import QdrantClient
from workmind.providers.llm import Done, LLMRequest, LLMUsage, Role, StreamEvent, TextDelta

QDRANT_URL = os.environ.get("TEST_QDRANT_URL")
OPENFGA_URL = os.environ.get("TEST_OPENFGA_URL")
OPENFGA_KEY = os.environ.get("TEST_OPENFGA_API_KEY")
pytestmark = pytest.mark.skipif(
    not (QDRANT_URL and VALKEY_URL and OPENFGA_URL),
    reason="set TEST_QDRANT_URL, TEST_VALKEY_URL and TEST_OPENFGA_URL",
)
LEAVE = b"# Leave Policy\n\n## Parental leave\nParental leave is 16 weeks, fully paid.\n"
PAYROLL = b"# Payroll Q3\n\n## Totals\nQ3 payroll was 6.1 million euros. CANARY-PAYROLL-2K8\n"


class FakeLLM:
    external = True

    def __init__(self) -> None:
        self.requests: list[LLMRequest] = []

    async def complete(self, role: Role, request: LLMRequest) -> tuple[str, LLMUsage]:
        raise NotImplementedError

    async def stream(self, role: Role, request: LLMRequest) -> AsyncIterator[StreamEvent]:
        self.requests.append(request)
        yield TextDelta("Parental leave is 16 weeks [S1].")
        yield Done(LLMUsage(model="fake", input_tokens=100, output_tokens=10))

    async def structured(self, role: Role, request: LLMRequest, schema: Any) -> Any:
        raise NotImplementedError


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
async def fga() -> AsyncIterator[OpenFgaClient]:
    async with httpx.AsyncClient(timeout=15) as http:
        yield OpenFgaClient(http, OPENFGA_URL or "", OPENFGA_KEY)


@pytest.fixture
async def synced(
    owner_sessions: SessionFactory, fga: OpenFgaClient
) -> AsyncIterator[tuple[Acme, str]]:
    acme = await seed_acme(owner_sessions)
    store = await ensure_store(owner_sessions, fga, acme.tenant_id)
    await reconcile(owner_sessions, fga, acme.tenant_id, store)
    yield acme, store
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
        object_storage_path=str(tmp_path),
    )
    with TestClient(create_app(settings)) as client:
        yield client


async def _collection(sessions: SessionFactory, acme: Acme, key: str) -> Collection:
    async with tenant_transaction(sessions, acme.tenant_id) as session:
        return (await session.scalars(select(Collection).where(Collection.key == key))).one()


def _upload(
    api: TestClient, acme: Acme, who: str, collection: uuid.UUID, name: str, data: bytes
) -> None:
    response = api.post(
        f"/api/v1/collections/{collection}/documents",
        params={"filename": name},
        content=data,
        headers={**acme.as_(who, write=True), "Content-Type": "text/markdown"},
    )
    assert response.status_code == 202, response.text
    document = response.json()["document"]["id"]
    deadline = time.monotonic() + 20
    while (
        api.get(f"/api/v1/documents/{document}", headers=acme.as_(who)).json()["status"] != "ready"
    ):
        assert time.monotonic() < deadline, "ingestion did not finish"
        time.sleep(0.2)


def _search(api: TestClient, acme: Acme, who: str, query: str) -> set[str]:
    response = api.post("/api/v1/search", json={"query": query}, headers=acme.as_(who, write=True))
    assert response.status_code == 200, response.text
    return {p["filename"] for p in response.json()["passages"]}


@pytest.fixture
async def corpus(
    api: TestClient, synced: tuple[Acme, str], app_sessions: SessionFactory
) -> tuple[Acme, str]:
    acme, store = synced
    handbook = await _collection(app_sessions, acme, "company-handbook")
    payroll = await _collection(app_sessions, acme, "finance-payroll")
    _upload(api, acme, "alice", handbook.id, "leave.md", LEAVE)
    _upload(api, acme, "hannah.schmidt", payroll.id, "payroll.md", PAYROLL)
    return acme, store


async def test_search_returns_only_what_each_person_may_read(
    api: TestClient, corpus: tuple[Acme, str]
) -> None:
    acme, _ = corpus

    assert "leave.md" in _search(api, acme, "bob", "parental leave weeks")
    assert "payroll.md" not in _search(api, acme, "bob", "Q3 payroll total")
    assert "payroll.md" in _search(api, acme, "hannah.schmidt", "Q3 payroll total")
    assert _search(api, acme, "mallory", "parental leave weeks") == set()


async def test_lock_two_drops_results_when_the_index_acl_is_out_of_date(
    api: TestClient, corpus: tuple[Acme, str], app_sessions: SessionFactory, fga: OpenFgaClient
) -> None:
    """Simulate drift: OpenFGA no longer lets the organization read the handbook, but the
    chunks in the index still carry the old ACL. Lock 2 must win."""
    acme, store = corpus
    handbook = await _collection(app_sessions, acme, "company-handbook")
    org_viewer = RelationTuple(
        f"organization:{acme.tenant_id}#member", "viewer", f"collection:{handbook.id}"
    )
    await fga.write(store, deletes=[org_viewer])
    try:
        assert "leave.md" not in _search(api, acme, "bob", "parental leave weeks")
    finally:
        await fga.write(store, writes=[org_viewer])
    assert "leave.md" in _search(api, acme, "bob", "parental leave weeks")


async def test_answers_cite_permitted_sources_and_abstain_without_calling_the_model(
    api: TestClient, corpus: tuple[Acme, str]
) -> None:
    acme, _ = corpus
    llm = FakeLLM()
    app: Any = api.app  # a FastAPI app; typed loosely by Starlette's TestClient
    app.state.resources.llm = llm

    answered = api.post(
        "/api/v1/ask",
        json={"question": "How long is parental leave?"},
        headers=acme.as_("alice", write=True),
    ).json()
    model_calls = len(llm.requests)
    refused = api.post(
        "/api/v1/ask",
        json={"question": "How long is parental leave?"},
        headers=acme.as_("mallory", write=True),
    ).json()

    assert answered["citations"][0]["filename"] == "leave.md"
    assert answered["abstained"] is False
    assert refused["abstained"] is True
    assert refused["sources"] == []
    assert len(llm.requests) == model_calls == 1  # the guest's question never reached a model
