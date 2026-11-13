"""A document's own access list narrows its collection's (ADR 0012, docs/permission-sync.md),
in the Community edition: everything here runs without a license.

Engineering Docs is open to the Engineering department. Marcus (its editor) limits one
document, carrying a canary, to Wei and himself. Bob is in Engineering and not on the list: he
must never see the document or its canary through search, chat, agents, research, MCP,
citations, the Knowledge Hub, analytics or the governance overview, and both locks, and every
other path that shows documents, must agree on who may see it. An unlimited document next to
it is the control: Bob sees that one.

Needs TEST_QDRANT_URL, TEST_VALKEY_URL and TEST_OPENFGA_URL (+ TEST_OPENFGA_API_KEY).
"""

import asyncio
import json
import os
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from tests.fakes import FakeLLM
from tests.integration.conftest import VALKEY_URL, Acme, Database, SessionFactory, seed_acme
from workmind.core.config import Settings
from workmind.core.context import RequestContext
from workmind.core.tenancy import tenant_transaction
from workmind.main import create_app
from workmind.modules.agents.department import EvidenceOut, Finding
from workmind.modules.agents.planning import PlanOut, Subtask
from workmind.modules.authz.openfga import OpenFgaClient, RelationTuple
from workmind.modules.authz.sync import ensure_store, process_outbox, reconcile
from workmind.modules.governance.audit import Actor
from workmind.modules.identity.models import Department, Group
from workmind.modules.identity.service import load_identity
from workmind.modules.knowledge import document_acl
from workmind.modules.knowledge.acl import acl_principals, can_view_document
from workmind.modules.knowledge.models import Collection, CollectionGrant, Document
from workmind.modules.retrieval.qdrant import QdrantClient, match
from workmind.modules.retrieval.service import Retriever
from workmind.providers.embeddings import HashEmbeddings

QDRANT_URL = os.environ.get("TEST_QDRANT_URL")
OPENFGA_URL = os.environ.get("TEST_OPENFGA_URL")
OPENFGA_KEY = os.environ.get("TEST_OPENFGA_API_KEY")
pytestmark = pytest.mark.skipif(
    not (QDRANT_URL and VALKEY_URL and OPENFGA_URL),
    reason="set TEST_QDRANT_URL, TEST_VALKEY_URL and TEST_OPENFGA_URL",
)
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
async def fga() -> AsyncIterator[OpenFgaClient]:
    async with httpx.AsyncClient(timeout=15) as http:
        yield OpenFgaClient(http, OPENFGA_URL or "", OPENFGA_KEY)


@pytest.fixture
async def qdrant() -> AsyncIterator[QdrantClient]:
    """The test's own client: the app's clients live on the TestClient's event loop."""
    async with httpx.AsyncClient(timeout=30) as http:
        yield QdrantClient(http, QDRANT_URL or "")


def retriever(
    qdrant: QdrantClient, fga: OpenFgaClient, sessions: SessionFactory, model_name: str
) -> Retriever:
    """The retriever the app builds, on the test's event loop."""
    return Retriever(
        qdrant=qdrant,
        embeddings=HashEmbeddings(64),
        fga=fga,
        sessions=sessions,
        collection_name=f"chunks_{model_name}_64",
    )


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
        retrieval_min_similarity=0.0,
        embedding_model=model_name,
        embedding_dimensions=64,
        object_storage_path=str(tmp_path),
        keycloak_url="http://127.0.0.1:9/auth",
    )
    with TestClient(create_app(settings)) as client:
        yield client


@dataclass
class World:
    acme: Acme
    store: str
    collection: uuid.UUID  # Engineering Docs
    limited: str  # the document on a list: Wei and Marcus
    open: str  # the control, with the collection's access
    canary: str

    def user(self, name: str) -> str:
        return f"user:{self.acme.users[name]}"


SECRET = (
    "# Maintenance token advisory\n\n## Details\nThe maintenance token outlives its session. {}\n"
)
OPEN = (
    "# Fleet controller restarts\n\n## Steps\nRestart the fleet controller with fleetctl restart.\n"
)


def upload(
    api: TestClient, acme: Acme, who: str, collection: uuid.UUID, name: str, data: str
) -> str:
    response = api.post(
        f"/api/v1/collections/{collection}/documents",
        params={"filename": name},
        content=data.encode(),
        headers={**acme.as_(who, write=True), "Content-Type": "text/markdown"},
    )
    assert response.status_code == 202, response.text
    document_id: str = response.json()["document"]["id"]
    deadline = time.monotonic() + 20
    while api.get(f"/api/v1/documents/{document_id}", headers=acme.as_(who)).json()[
        "status"
    ] not in ("ready", "failed"):
        assert time.monotonic() < deadline, "ingestion did not finish"
        time.sleep(0.1)
    return document_id


def finished_runs(api: TestClient, headers: dict[str, str], workflow: str) -> list[Any]:
    deadline = time.monotonic() + 20
    runs: list[Any] = []
    while time.monotonic() < deadline:
        runs = api.get(f"/api/v1/admin/workflows/{workflow}/runs", headers=headers).json()
        if runs and all(r["status"] not in ("queued", "running") for r in runs):
            break
        time.sleep(0.2)
    return runs


def set_list(api: TestClient, w: World, who: str, document: str, viewers: list[str] | None) -> Any:
    return api.put(
        f"/api/v1/documents/{document}/acl",
        json={"viewers": viewers},
        headers=w.acme.as_(who, write=True),
    )


async def drain(sessions: SessionFactory, fga: OpenFgaClient, w: World) -> None:
    while await process_outbox(sessions, fga, w.acme.tenant_id, w.store):
        pass


@pytest.fixture
async def world(
    api: TestClient,
    owner_sessions: SessionFactory,
    app_sessions: SessionFactory,
    fga: OpenFgaClient,
) -> AsyncIterator[World]:
    acme = await seed_acme(owner_sessions)
    store = await ensure_store(owner_sessions, fga, acme.tenant_id)
    await reconcile(owner_sessions, fga, acme.tenant_id, store)
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        collection = (
            await session.scalars(select(Collection.id).where(Collection.key == "engineering-docs"))
        ).one()
    canary = f"CANARY-ACL-{uuid.uuid4().hex[:8].upper()}"
    limited = upload(api, acme, "marcus.bell", collection, "advisory.md", SECRET.format(canary))
    control = upload(api, acme, "marcus.bell", collection, "restarts.md", OPEN)
    w = World(acme, store, collection, limited, control, canary)
    response = set_list(
        api, w, "marcus.bell", limited, [w.user("wei.zhang"), w.user("marcus.bell")]
    )
    assert response.status_code == 200, response.text
    await drain(app_sessions, fga, w)
    yield w
    await fga.delete_store(store)


def search(api: TestClient, w: World, who: str, query: str, mode: str = "hybrid") -> str:
    response = api.post(
        "/api/v1/search",
        json={"query": query, "limit": 20, "mode": mode},
        headers=w.acme.as_(who, write=True),
    )
    assert response.status_code == 200, response.text
    return response.text


def use_model(api: TestClient, llm: FakeLLM) -> FakeLLM:
    app: Any = api.app
    app.state.resources.llm = llm
    return llm


def converse(api: TestClient, w: World, who: str, text: str) -> str:
    """One chat turn, start to end: every event of the run, as text."""
    conversation = api.post("/api/v1/conversations", json={}, headers=w.acme.as_(who, write=True))
    assert conversation.status_code == 201, conversation.text
    started = api.post(
        f"/api/v1/conversations/{conversation.json()['id']}/messages",
        json={"content": text},
        headers=w.acme.as_(who, write=True),
    )
    assert started.status_code == 202, started.text
    received: list[str] = []
    with api.stream(
        "GET", f"/api/v1/runs/{started.json()['run_id']}/events", headers=w.acme.as_(who)
    ) as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            received.append(line)
            if line.startswith("event: run.") and line[7:] != "run.started":
                break
    stored = api.get(f"/api/v1/conversations/{conversation.json()['id']}", headers=w.acme.as_(who))
    return "\n".join(received) + stored.text


def mcp(api: TestClient, w: World, who: str, tool: str, **arguments: Any) -> str:
    created = api.post(
        "/api/v1/me/tokens",
        json={"name": f"mcp-{uuid.uuid4().hex[:6]}", "scopes": ["knowledge:read", "assistant:ask"]},
        headers=w.acme.as_(who, write=True),
    )
    assert created.status_code == 201, created.text
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }
    response = api.post(
        "/mcp",
        content=json.dumps(body),
        headers={**MCP_HEADERS, "Authorization": f"Bearer {created.json()['token']}"},
    )
    assert response.status_code == 200, response.text
    return response.text


async def test_bob_never_sees_a_document_whose_list_leaves_him_out(
    api: TestClient, world: World
) -> None:
    w = world
    query = "maintenance token advisory outlives its session"
    for mode in ("hybrid", "dense", "sparse"):
        found = search(api, w, "bob", query, mode)
        assert w.canary not in found, mode
        assert "advisory.md" not in found, mode
    assert "restarts.md" in search(api, w, "bob", "restart the fleet controller")
    # The control: the people on the list find it.
    assert w.canary in search(api, w, "wei.zhang", query)
    assert w.canary in search(api, w, "marcus.bell", query)

    # Chat (single assistant): nothing about it reaches the model, the answer or its citations.
    llm = use_model(api, FakeLLM("The maintenance token outlives its session [S1]."))
    chat = converse(api, w, "bob", "What does the maintenance token advisory say?")
    asked = api.post("/api/v1/ask", json={"question": query}, headers=w.acme.as_("bob", write=True))
    assert asked.status_code == 200, asked.text
    for text in (chat, asked.text, repr(llm.requests)):
        assert w.canary not in text
        assert "advisory.md" not in text

    # Research: the search tool's results are what the model reads.
    llm = use_model(
        api,
        FakeLLM(tool_turns=[[("search_knowledge", {"query": query, "limit": 10})]], tool_text="."),
    )
    research = api.post(
        "/api/v1/research",
        json={"question": query, "tools": ["search_knowledge"]},
        headers=w.acme.as_("bob", write=True),
    )
    assert research.status_code == 200, research.text
    assert llm.tool_replies
    assert all(w.canary not in r.content for r in llm.tool_replies)
    assert w.canary not in research.text

    # Agents: the Engineering agent searches Engineering Docs as Bob.
    llm = use_model(
        api,
        FakeLLM(
            "Nothing found.",
            structured=[
                PlanOut(mode="single", subtasks=[Subtask(agent="engineering", goal=query)]),
                EvidenceOut(
                    findings=[Finding(claim="-", sources=["S1"])], gaps=[], follow_up_query=""
                ),
            ],
        ),
    )
    app: Any = api.app
    app.state.resources.assistant_mode, app.state.resources.agent_router = "agents", None
    try:
        agents = converse(api, w, "bob", query)
    finally:
        app.state.resources.assistant_mode = "single"
    assert [role for role, _ in llm.requests][:2] == ["router", "agent"]
    assert w.canary not in agents
    assert w.canary not in repr(llm.requests)

    # MCP: search, ask and the collection list (whose count leaves it out).
    use_model(api, FakeLLM("Nothing [S1]."))
    for tool, arguments in (
        ("search_knowledge", {"query": query}),
        ("ask", {"question": query}),
        ("list_collections", {}),
    ):
        text = mcp(api, w, "bob", tool, **arguments)
        assert w.canary not in text, tool
        assert "advisory" not in text, tool
    assert "Engineering Docs (1 documents)" in mcp(api, w, "bob", "list_collections")
    assert "Engineering Docs (2 documents)" in mcp(api, w, "wei.zhang", "list_collections")


async def test_the_knowledge_hub_answers_as_if_it_did_not_exist(
    api: TestClient, world: World
) -> None:
    w, bob = world, world.acme.as_("bob")
    for path in ("", "/chunks", "/file", "/acl"):
        response = api.get(f"/api/v1/documents/{w.limited}{path}", headers=bob)
        missing = api.get(f"/api/v1/documents/{uuid.uuid4()}{path}", headers=bob)
        assert (response.status_code, response.json()) == (missing.status_code, missing.json())
        assert response.status_code == 404
    for method, path, body in (
        ("PATCH", "", {"trust": "discussion"}),
        ("POST", "/review", {"note": "x"}),
        ("POST", "/reindex", None),
        ("PUT", "/acl", {"viewers": None}),
        ("DELETE", "", None),
    ):
        response = api.request(
            method,
            f"/api/v1/documents/{w.limited}{path}",
            json=body,
            headers=w.acme.as_("bob", write=True),
        )
        assert response.status_code == 404, (method, path, response.text)
    listed = api.get(f"/api/v1/collections/{w.collection}/documents", headers=bob).json()
    assert [d["id"] for d in listed["items"]] == [w.open]
    collection = api.get(f"/api/v1/collections/{w.collection}", headers=bob).json()
    assert collection["document_count"] == 1
    # The people on the list see it, marked as having its own list.
    wei = api.get(f"/api/v1/documents/{w.limited}", headers=w.acme.as_("wei.zhang"))
    assert wei.status_code == 200
    assert wei.json()["own_acl"] is True
    chunks = api.get(f"/api/v1/documents/{w.limited}/chunks", headers=w.acme.as_("wei.zhang"))
    assert w.canary in chunks.text
    # Analytics and usage never carry document content: nothing to find there either.
    for path in ("/api/v1/analytics/model", "/api/v1/analytics/runs"):
        assert w.canary not in api.get(path, headers=bob).text


async def test_both_locks_agree_for_every_person_on_every_document(
    world: World,
    app_sessions: SessionFactory,
    fga: OpenFgaClient,
    qdrant: QdrantClient,
    model_name: str,
) -> None:
    """Lock 1 as PostgreSQL computes it (the Knowledge Hub's rule), lock 1 as the Qdrant filter
    applies it, and lock 2 (OpenFGA) give the same answer for every person."""
    w = world
    search_filter = retriever(qdrant, fga, app_sessions, model_name).acl_filter
    name = f"chunks_{model_name}_64"
    disagreements: list[str] = []
    allowed: set[str] = set()
    async with tenant_transaction(app_sessions, w.acme.tenant_id) as session:
        collection = await session.get(Collection, w.collection)
        assert collection is not None
        grants = (
            await session.scalars(
                select(CollectionGrant).where(CollectionGrant.collection_id == w.collection)
            )
        ).all()
        documents = {d: await session.get(Document, uuid.UUID(d)) for d in (w.limited, w.open)}
        lists = await document_acl.grants_of(session, [uuid.UUID(d) for d in documents])
        identities = {n: await load_identity(session, u) for n, u in w.acme.users.items()}
    for person, identity in identities.items():
        assert identity is not None
        ctx = RequestContext(
            request_id="t",
            tenant_id=w.acme.tenant_id,
            user_id=identity.user.id,
            org_role=identity.user.org_role,
            principals=identity.principals,
            auth_method="service",
        )
        for document_id, document in documents.items():
            assert document is not None
            postgres = bool(
                set(acl_principals(collection, grants)) & identity.principals
            ) and can_view_document(identity.principals, document, lists[document.id])
            flt = search_filter(ctx)
            flt["must"].append(match("document_id", document_id))
            index = await qdrant.count(name, flt, tenant_id=w.acme.tenant_id) > 0
            openfga = await fga.check(
                w.store,
                RelationTuple(f"user:{identity.user.id}", "viewer", f"document:{document_id}"),
            )
            if len({postgres, index, openfga}) != 1:
                disagreements.append(
                    f"{person} / {document.filename}: pg={postgres} index={index} fga={openfga}"
                )
            if document_id == w.limited and postgres:
                allowed.add(person)
    assert disagreements == []
    assert allowed == {"wei.zhang", "marcus.bell"}


async def test_lock_two_holds_when_the_index_forgets_the_list(
    world: World,
    app_sessions: SessionFactory,
    fga: OpenFgaClient,
    qdrant: QdrantClient,
    model_name: str,
) -> None:
    """Simulated drift: the chunks lose their list (as if a payload refresh failed). PostgreSQL
    still says the document has one, so lock 2 asks OpenFGA about the document itself."""
    w = world
    await qdrant.set_payload(
        f"chunks_{model_name}_64",
        {"own_acl": False, "doc_acl": []},
        {"must": [match("document_id", w.limited)]},
        tenant_id=w.acme.tenant_id,
    )
    async with tenant_transaction(app_sessions, w.acme.tenant_id) as session:
        identity = await load_identity(session, w.acme.users["bob"])
    assert identity is not None
    ctx = RequestContext(
        request_id="t",
        tenant_id=w.acme.tenant_id,
        user_id=identity.user.id,
        org_role="member",
        principals=identity.principals,
        auth_method="service",
    )

    result = await retriever(qdrant, fga, app_sessions, model_name).search(
        ctx, "maintenance token advisory session", limit=20
    )

    assert all(p.document_id != w.limited for p in result.passages)
    assert result.dropped_by_recheck >= 1  # counted as drift


async def test_a_new_list_holds_before_openfga_and_the_index_catch_up(
    world: World,
    app_sessions: SessionFactory,
    fga: OpenFgaClient,
    qdrant: QdrantClient,
    model_name: str,
) -> None:
    """The moment after the commit: the outbox has not reached OpenFGA and the chunks still
    say the document has no list. PostgreSQL, read by lock 2, already says Bob is not on it."""
    w = world
    async with tenant_transaction(app_sessions, w.acme.tenant_id) as session:
        document = await session.get(Document, uuid.UUID(w.open))
        assert document is not None
        await document_acl.set_acl(
            session,
            w.acme.tenant_id,
            document,
            [document_acl.AclEntry(w.user("wei.zhang"))],
            origin="manual",
            actor=Actor.system(),
        )
    assert await fga.check(w.store, RelationTuple(w.user("bob"), "viewer", f"document:{w.open}"))
    search = retriever(qdrant, fga, app_sessions, model_name).search

    bob = await search(await _ctx(app_sessions, w, "bob"), "restart the fleet controller")
    wei = await search(await _ctx(app_sessions, w, "wei.zhang"), "restart the fleet controller")

    assert all(p.document_id != w.open for p in bob.passages)
    assert bob.dropped_by_recheck >= 1
    assert any(p.document_id == w.open for p in wei.passages)


async def _ctx(sessions: SessionFactory, w: World, name: str) -> RequestContext:
    async with tenant_transaction(sessions, w.acme.tenant_id) as session:
        identity = await load_identity(session, w.acme.users[name])
    assert identity is not None
    return RequestContext(
        request_id="t",
        tenant_id=w.acme.tenant_id,
        user_id=identity.user.id,
        org_role=identity.user.org_role,
        principals=identity.principals,
        auth_method="service",
    )


async def test_a_list_never_opens_the_collection(
    api: TestClient, world: World, app_sessions: SessionFactory, fga: OpenFgaClient
) -> None:
    w = world
    # Priya is on the list but not in Engineering: the list only narrows.
    response = set_list(
        api, w, "marcus.bell", w.limited, [w.user("marcus.bell"), w.user("priya.raman")]
    )
    assert response.status_code == 200, response.text
    await drain(app_sessions, fga, w)
    assert w.canary not in search(api, w, "priya.raman", "maintenance token advisory")
    assert (
        api.get(f"/api/v1/documents/{w.limited}", headers=w.acme.as_("priya.raman")).status_code
        == 404
    )
    assert not await fga.check(
        w.store, RelationTuple(w.user("priya.raman"), "viewer", f"document:{w.limited}")
    )
    # Wei left the list: at once, in search, the Knowledge Hub and OpenFGA.
    assert w.canary not in search(api, w, "wei.zhang", "maintenance token advisory")
    assert (
        api.get(f"/api/v1/documents/{w.limited}", headers=w.acme.as_("wei.zhang")).status_code
        == 404
    )
    assert not await fga.check(
        w.store, RelationTuple(w.user("wei.zhang"), "viewer", f"document:{w.limited}")
    )


async def test_editors_set_and_lift_lists_within_the_rules(
    api: TestClient, world: World, app_sessions: SessionFactory, fga: OpenFgaClient
) -> None:
    w = world
    # Wei can read it but cannot change the collection; Bob cannot even see it.
    assert set_list(api, w, "wei.zhang", w.limited, None).status_code == 403
    assert set_list(api, w, "bob", w.limited, None).status_code == 404
    # Nobody locks themselves out, and only this tenant's people can be named.
    lockout = set_list(api, w, "marcus.bell", w.limited, [w.user("wei.zhang")])
    assert (lockout.status_code, lockout.json()["code"]) == (422, "would_lose_access")
    foreign = set_list(
        api, w, "marcus.bell", w.limited, [w.user("marcus.bell"), f"user:{uuid.uuid4()}"]
    )
    assert (foreign.status_code, foreign.json()["code"]) == (422, "unknown_principal")
    detail = api.get(f"/api/v1/documents/{w.limited}/acl", headers=w.acme.as_("marcus.bell")).json()
    assert detail["own_acl"]
    assert detail["origin"] == "manual"
    assert {e["principal"] for e in detail["entries"]} == {
        w.user("wei.zhang"),
        w.user("marcus.bell"),
    }

    # Lifting the list gives the document its collection's access again, everywhere.
    lifted = set_list(api, w, "marcus.bell", w.limited, None)
    assert lifted.status_code == 200
    assert lifted.json()["own_acl"] is False
    await drain(app_sessions, fga, w)
    assert w.canary in search(api, w, "bob", "maintenance token advisory outlives its session")
    assert api.get(f"/api/v1/documents/{w.limited}", headers=w.acme.as_("bob")).status_code == 200
    assert await fga.check(w.store, RelationTuple(w.user("bob"), "viewer", f"document:{w.limited}"))

    # A department as the list: Bob is in Engineering, Dan is not (and not in the collection).
    async with tenant_transaction(app_sessions, w.acme.tenant_id) as session:
        engineering = (
            await session.scalars(select(Department.id).where(Department.slug == "engineering"))
        ).one()
    again = set_list(api, w, "marcus.bell", w.limited, [f"dept:{engineering}"])
    assert again.status_code == 200, again.text
    await drain(app_sessions, fga, w)
    assert w.canary in search(api, w, "bob", "maintenance token advisory outlives its session")
    assert w.canary not in search(api, w, "dan", "maintenance token advisory outlives its session")


async def test_a_list_nobody_could_be_mapped_to_hides_it_from_everyone_until_mapped(
    api: TestClient,
    world: World,
    app_sessions: SessionFactory,
    fga: OpenFgaClient,
    qdrant: QdrantClient,
    model_name: str,
) -> None:
    """As permission sync writes it when the source names only a group WorkMind does not know:
    nobody sees the document (not its editors, not the people the group would include), and
    admins see the group listed, without the document's title. Mapping the group opens the
    document to its members who can read the collection, at once; removing the mapping closes
    it again."""
    w = world
    external = "group:incident-team@acme.test"
    async with tenant_transaction(app_sessions, w.acme.tenant_id) as session:
        document = await session.get(Document, uuid.UUID(w.limited))
        assert document is not None
        await document_acl.set_acl(
            session,
            w.acme.tenant_id,
            document,
            await document_acl.entries_for(session, w.acme.tenant_id, [external]),
            origin="source",
            actor=Actor.system(),
        )
        payload = await document_acl.payload_of(session, document)
        leadership = (
            await session.scalars(select(Group.id).where(Group.slug == "leadership"))
        ).one()
    await document_acl.refresh_payload(
        qdrant, f"chunks_{model_name}_64", w.acme.tenant_id, document.id, payload
    )
    await drain(app_sessions, fga, w)
    query = "maintenance token advisory outlives its session"
    for person in ("marcus.bell", "wei.zhang", "bob"):
        assert w.canary not in search(api, w, person, query), person
        assert (
            api.get(f"/api/v1/documents/{w.limited}", headers=w.acme.as_(person)).status_code == 404
        )

    erin = w.acme.as_("erin", write=True)
    listed = api.get("/api/v1/admin/identity-mappings", headers=erin).json()
    assert listed["unmapped"] == [{"external": external, "documents": 1, "sources": []}]
    assert "advisory" not in json.dumps(listed)
    # Only admins see or change mappings.
    assert (
        api.get("/api/v1/admin/identity-mappings", headers=w.acme.as_("marcus.bell")).status_code
        == 403
    )

    saved = api.put(
        "/api/v1/admin/identity-mappings",
        json={"external": "Group:Incident-Team@acme.test", "principal": f"group:{leadership}"},
        headers=erin,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["documents_changed"] == 1
    assert saved.json()["mapping"]["external"] == external  # stored lower case
    await drain(app_sessions, fga, w)
    # Marcus is in leadership and in Engineering; Priya is in leadership, not in Engineering.
    assert w.canary in search(api, w, "marcus.bell", query)
    assert w.canary not in search(api, w, "priya.raman", query)
    assert w.canary not in search(api, w, "bob", query)
    assert api.get("/api/v1/admin/identity-mappings", headers=erin).json()["unmapped"] == []

    removed = api.delete(
        f"/api/v1/admin/identity-mappings/{saved.json()['mapping']['id']}", headers=erin
    )
    assert removed.status_code == 204
    await drain(app_sessions, fga, w)
    assert w.canary not in search(api, w, "marcus.bell", query)
    assert not await fga.check(
        w.store, RelationTuple(w.user("marcus.bell"), "viewer", f"document:{w.limited}")
    )
    bad = api.put(
        "/api/v1/admin/identity-mappings",
        json={"external": "not a principal", "principal": f"group:{leadership}"},
        headers=erin,
    )
    assert bad.status_code == 422


async def test_people_who_manage_but_cannot_read_it_never_see_its_title(
    api: TestClient, world: World, app_sessions: SessionFactory, fga: OpenFgaClient
) -> None:
    """Erin (admin) joins Engineering, so she can open the collection but is not on the list:
    the governance overview, the stale-documents list and approvals withhold the title; she
    cannot become its owner, its approver, or receive a workflow run about it."""
    w = world
    erin = w.acme.as_("erin", write=True)
    async with tenant_transaction(app_sessions, w.acme.tenant_id) as session:
        engineering = (
            await session.scalars(select(Department.id).where(Department.slug == "engineering"))
        ).one()
        await session.execute(
            update(Document)
            .where(Document.id.in_([uuid.UUID(w.limited), uuid.UUID(w.open)]))
            .values(next_review_at=datetime.now(UTC) - timedelta(days=3))
        )
    joined = api.put(
        f"/api/v1/admin/departments/{engineering}/members/{w.acme.users['erin']}",
        json={"role": "member"},
        headers=erin,
    )
    assert joined.status_code in (200, 204), joined.text
    await drain(app_sessions, fga, w)

    overview = api.get("/api/v1/governance/overview", headers=w.acme.as_("erin")).json()
    titles = {d["id"]: d["title"] for d in overview["overdue"]}
    assert titles[w.limited] is None
    assert titles[w.open] == "Fleet controller restarts"
    stale = api.get("/api/v1/admin/sources/stale", params={"days": 0}, headers=erin).json()
    titles = {d["id"]: d["title"] for d in stale}
    assert titles[w.limited] is None
    assert titles[w.open] is not None

    # Owners and approvers must be able to read it.
    owner = api.put(
        f"/api/v1/documents/{w.limited}/owner",
        json={"owner_id": str(w.acme.users["bob"])},
        headers=w.acme.as_("marcus.bell", write=True),
    )
    assert (owner.status_code, owner.json()["code"]) == (422, "owner_cannot_view")
    official = api.post(
        f"/api/v1/documents/{w.limited}/official",
        json={"note": "Ready"},
        headers=w.acme.as_("marcus.bell", write=True),
    )
    # Marcus owns the collection and asks himself; Erin, the admin who could decide, cannot
    # read the document: nobody may approve it.
    assert (official.status_code, official.json()["code"]) == (409, "no_approver")

    # A workflow Erin owns listens to Engineering Docs: a new version of the limited document
    # starts no run for her; the open one does.
    llm = use_model(api, FakeLLM(tool_turns=[], tool_text="Summary."))
    saved = api.put(
        "/api/v1/admin/workflows/eng-watch",
        json={
            "key": "eng-watch",
            "name": "Eng watch",
            "definition": {
                "trigger": {"event": "document.ready", "collection": "engineering-docs"},
                "steps": [{"id": "read", "research": "Summarize {{ event.title }}."}],
            },
        },
        headers=erin,
    )
    assert saved.status_code == 200, saved.text
    upload(
        api, w.acme, "marcus.bell", w.collection, "advisory.md", SECRET.format(w.canary) + "v2\n"
    )
    upload(api, w.acme, "marcus.bell", w.collection, "restarts.md", OPEN + "v2\n")
    runs = finished_runs(api, erin, "eng-watch")
    assert len(runs) == 1
    assert w.canary not in json.dumps(runs)
    assert w.canary not in repr(llm.requests)
    assert "advisory" not in repr(llm.requests)
