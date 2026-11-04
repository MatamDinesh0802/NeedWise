"""Conversations over real HTTP: runs, Server-Sent Events, follow-ups, privacy, cancellation
and feedback. Retrieval is real (Qdrant + OpenFGA, hash embeddings); the model is scripted.

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

from tests.fakes import FakeLLM
from tests.integration.conftest import VALKEY_URL, Acme, Database, SessionFactory, seed_acme
from workmind.core.config import Settings
from workmind.core.tenancy import tenant_transaction
from workmind.main import create_app
from workmind.modules.authz.openfga import OpenFgaClient
from workmind.modules.authz.sync import ensure_store, reconcile
from workmind.modules.knowledge.models import Collection
from workmind.modules.retrieval.qdrant import QdrantClient

QDRANT_URL = os.environ.get("TEST_QDRANT_URL")
OPENFGA_URL = os.environ.get("TEST_OPENFGA_URL")
OPENFGA_KEY = os.environ.get("TEST_OPENFGA_API_KEY")
pytestmark = pytest.mark.skipif(
    not (QDRANT_URL and VALKEY_URL and OPENFGA_URL),
    reason="set TEST_QDRANT_URL, TEST_VALKEY_URL and TEST_OPENFGA_URL",
)
LEAVE = b"# Leave Policy\n\n## Parental leave\nParental leave is 16 weeks, fully paid.\n"


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
        retrieval_min_similarity=0.0,  # the floor is calibrated for real embeddings
        embedding_model=model_name,
        embedding_dimensions=64,
        object_storage_path=str(tmp_path),
    )
    with TestClient(create_app(settings)) as client:
        yield client


@pytest.fixture
async def acme(api: TestClient, synced: Acme, app_sessions: SessionFactory) -> Acme:
    async with tenant_transaction(app_sessions, synced.tenant_id) as session:
        handbook = (
            await session.scalars(select(Collection).where(Collection.key == "company-handbook"))
        ).one()
    uploaded = api.post(
        f"/api/v1/collections/{handbook.id}/documents",
        params={"filename": "leave.md"},
        content=LEAVE,
        headers={**synced.as_("alice", write=True), "Content-Type": "text/markdown"},
    ).json()["document"]["id"]
    deadline = time.monotonic() + 20
    while (
        api.get(f"/api/v1/documents/{uploaded}", headers=synced.as_("alice")).json()["status"]
        != "ready"
    ):
        assert time.monotonic() < deadline
        await asyncio.sleep(0.2)
    return synced


def use_model(api: TestClient, llm: FakeLLM) -> FakeLLM:
    app: Any = api.app
    app.state.resources.llm = llm
    return llm


def start(api: TestClient, acme: Acme, who: str) -> str:
    response = api.post("/api/v1/conversations", json={}, headers=acme.as_(who, write=True))
    assert response.status_code == 201, response.text
    return response.json()["id"]


def ask(api: TestClient, acme: Acme, who: str, conversation: str, text: str) -> str:
    response = api.post(
        f"/api/v1/conversations/{conversation}/messages",
        json={"content": text},
        headers=acme.as_(who, write=True),
    )
    assert response.status_code == 202, response.text
    return response.json()["run_id"]


def events(
    api: TestClient, acme: Acme, who: str, run: str, **headers: str
) -> list[tuple[str, dict[str, Any]]]:
    received: list[tuple[str, dict[str, Any]]] = []
    name = ""
    with api.stream(
        "GET", f"/api/v1/runs/{run}/events", headers={**acme.as_(who), **headers}
    ) as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                received.append((name, json.loads(line[6:])))
                if name in ("run.completed", "run.failed", "run.cancelled"):
                    break
    return received


async def test_a_question_streams_sources_tokens_and_citations_then_is_stored(
    api: TestClient, acme: Acme
) -> None:
    use_model(api, FakeLLM(["Parental leave ", "is 16 weeks ", "[S1]."]))
    conversation = start(api, acme, "alice")

    run = ask(api, acme, "alice", conversation, "How long is parental leave?")
    received = events(api, acme, "alice", run)
    detail = api.get(f"/api/v1/conversations/{conversation}", headers=acme.as_("alice")).json()

    names = [name for name, _ in received]
    assert names[0] == "run.started"
    assert names[1] == "retrieval"
    assert (
        "".join(d["text"] for n, d in received if n == "token")
        == "Parental leave is 16 weeks [S1]."
    )
    assert ("citation", "leave.md") in {(n, d.get("title", "") and "leave.md") for n, d in received}
    assert names[-1] == "run.completed"
    assert received[1][1]["sources"][0]["text"].startswith("Parental leave")  # live, for the reader
    assert detail["title"] == "How long is parental leave?"
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    stored = detail["messages"][1]
    assert stored["citations"][0]["filename"] == "leave.md"
    assert "text" not in stored["sources"][0]  # source text is never copied into history
    assert stored["usage"]["output_tokens"] == 3


async def test_events_can_be_replayed_from_the_last_one_seen(api: TestClient, acme: Acme) -> None:
    use_model(api, FakeLLM(["a ", "b ", "c [S1]"]))
    conversation = start(api, acme, "alice")
    run = ask(api, acme, "alice", conversation, "How long is parental leave?")
    everything = events(api, acme, "alice", run)

    with api.stream("GET", f"/api/v1/runs/{run}/events", headers=acme.as_("alice")) as response:
        first_id = next(line[4:] for line in response.iter_lines() if line.startswith("id: "))
    replay = events(api, acme, "alice", run, **{"Last-Event-ID": first_id})

    assert [n for n, _ in replay] == [n for n, _ in everything][1:]


async def test_a_follow_up_is_rewritten_before_searching(api: TestClient, acme: Acme) -> None:
    llm = use_model(api, FakeLLM(rewrite="parental leave duration for contractors"))
    conversation = start(api, acme, "alice")
    events(api, acme, "alice", ask(api, acme, "alice", conversation, "How long is parental leave?"))

    received = events(
        api, acme, "alice", ask(api, acme, "alice", conversation, "and for contractors?")
    )
    detail = api.get(f"/api/v1/conversations/{conversation}", headers=acme.as_("alice")).json()

    assert ("query.rewritten", {"query": "parental leave duration for contractors"}) in received
    assert detail["messages"][-1]["search_query"] == "parental leave duration for contractors"
    answer_prompt = llm.requests[-1][1]
    assert [m.role for m in answer_prompt.messages] == ["user", "assistant", "user"]
    assert "[S1]" not in answer_prompt.messages[1].content  # old markers are stripped


async def test_conversations_and_runs_are_private(api: TestClient, acme: Acme) -> None:
    use_model(api, FakeLLM())
    conversation = start(api, acme, "alice")
    run = ask(api, acme, "alice", conversation, "How long is parental leave?")
    events(api, acme, "alice", run)

    assert (
        api.get(f"/api/v1/conversations/{conversation}", headers=acme.as_("bob")).status_code == 404
    )
    assert (
        api.get(f"/api/v1/conversations/{conversation}", headers=acme.as_("erin")).status_code
        == 404
    )
    assert api.get(f"/api/v1/runs/{run}/events", headers=acme.as_("bob")).status_code == 404
    assert conversation not in {
        c["id"] for c in api.get("/api/v1/conversations", headers=acme.as_("bob")).json()["items"]
    }


async def test_one_answer_at_a_time_and_cancel_stops_it(api: TestClient, acme: Acme) -> None:
    use_model(api, FakeLLM([f"word{i} " for i in range(200)], delay=0.01))
    conversation = start(api, acme, "alice")
    run = ask(api, acme, "alice", conversation, "How long is parental leave?")

    second = api.post(
        f"/api/v1/conversations/{conversation}/messages",
        json={"content": "again"},
        headers=acme.as_("alice", write=True),
    )
    cancelled = api.post(f"/api/v1/runs/{run}/cancel", headers=acme.as_("alice", write=True))
    received = events(api, acme, "alice", run)
    detail = api.get(f"/api/v1/conversations/{conversation}", headers=acme.as_("alice")).json()

    assert (second.status_code, second.json()["code"]) == (409, "run_in_progress")
    assert cancelled.status_code == 202
    assert received[-1][0] == "run.cancelled"
    assert detail["messages"][-1]["status"] == "cancelled"
    assert detail["active_run_id"] is None


async def test_feedback_is_recorded_on_answers(api: TestClient, acme: Acme) -> None:
    use_model(api, FakeLLM())
    conversation = start(api, acme, "alice")
    received = events(api, acme, "alice", ask(api, acme, "alice", conversation, "Leave?"))
    message = received[-1][1]["message_id"]

    rated = api.post(
        f"/api/v1/messages/{message}/feedback",
        json={"rating": "down", "comment": "Missing the 2025 rules"},
        headers=acme.as_("alice", write=True),
    )
    by_someone_else = api.post(
        f"/api/v1/messages/{message}/feedback",
        json={"rating": "up"},
        headers=acme.as_("bob", write=True),
    )

    assert (rated.json()["feedback"], rated.json()["feedback_comment"]) == (
        "down",
        "Missing the 2025 rules",
    )
    assert by_someone_else.status_code == 404


async def test_a_guest_gets_an_honest_answer_without_any_model_call(
    api: TestClient, acme: Acme
) -> None:
    llm = use_model(api, FakeLLM())
    conversation = start(api, acme, "mallory")

    received = events(
        api, acme, "mallory", ask(api, acme, "mallory", conversation, "How long is parental leave?")
    )

    assert received[1] == ("retrieval", {"sources": [], "withheld": 0})
    assert received[-1][1]["abstained"] is True
    assert llm.requests == []


async def test_in_agents_mode_the_plan_and_each_agent_are_streamed(
    api: TestClient, acme: Acme
) -> None:
    from workmind.modules.agents.department import EvidenceOut, Finding
    from workmind.modules.agents.planning import PlanOut, Subtask

    llm = use_model(
        api,
        FakeLLM(
            "Parental leave is 16 weeks [S1].",
            structured=[
                PlanOut(
                    mode="single", subtasks=[Subtask(agent="hr", goal="Parental leave length?")]
                ),
                EvidenceOut(
                    findings=[Finding(claim="16 weeks", sources=["S1"])],
                    gaps=[],
                    follow_up_query="",
                ),
            ],
        ),
    )
    app: Any = api.app
    app.state.resources.assistant_mode = "agents"
    app.state.resources.agent_router = None  # always ask the (scripted) planner
    conversation = start(api, acme, "alice")

    received = events(api, acme, "alice", ask(api, acme, "alice", conversation, "Parental leave?"))
    detail = api.get(f"/api/v1/conversations/{conversation}", headers=acme.as_("alice")).json()

    names = [n for n, _ in received]
    assert names[:3] == ["run.started", "plan", "agent.step"]
    assert received[1][1] == {
        "mode": "single",
        "routed_by": "planner",
        "subtasks": [{"agent": "hr", "goal": "Parental leave length?", "name": "HR agent"}],
    }
    done = next(d for n, d in received if n == "agent.step" and d["status"] == "done")
    assert (done["agent"], done["name"], done["findings"]) == ("hr", "HR agent", 1)
    assert detail["messages"][-1]["citations"][0]["filename"] == "leave.md"
    assert [role for role, _ in llm.requests] == ["router", "agent", "answer"]
    assert detail["messages"][-1]["usage"]["input_tokens"] == 30 + 30 + 100  # every call counted


async def test_a_cancelled_exchange_does_not_steer_the_next_answer(
    api: TestClient, acme: Acme
) -> None:
    llm = use_model(api, FakeLLM([f"word{i} " for i in range(200)], delay=0.01))
    conversation = start(api, acme, "alice")
    run = ask(api, acme, "alice", conversation, "Explain the whole travel policy in detail")
    api.post(f"/api/v1/runs/{run}/cancel", headers=acme.as_("alice", write=True))
    events(api, acme, "alice", run)
    llm.tokens, llm.delay = ["Sixteen weeks [S1]."], 0.0

    received = events(api, acme, "alice", ask(api, acme, "alice", conversation, "Parental leave?"))

    prompt = llm.requests[-1][1]
    assert [m.role for m in prompt.messages] == ["user"]  # no unanswered question before it
    assert received[-1][1]["usage"]["cost_usd"] is None  # the fake model has no price
    assert received[-1][1]["usage"]["input_tokens"] == 100  # no empty rewrite usage counted


async def test_a_long_pause_between_events_keeps_the_stream_alive(
    api: TestClient, acme: Acme
) -> None:
    """Regression: a pause longer than the Valkey client's 5 s socket timeout used to abort
    the SSE response mid-stream (browsers showed ERR_INCOMPLETE_CHUNKED_ENCODING)."""
    use_model(api, FakeLLM(["Sixteen weeks [S1]."], delay=6.0))
    conversation = start(api, acme, "alice")

    received = events(api, acme, "alice", ask(api, acme, "alice", conversation, "Leave?"))

    assert received[-1][0] == "run.completed"
