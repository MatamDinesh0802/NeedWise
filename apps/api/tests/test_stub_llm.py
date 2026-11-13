"""The load-test model stub (loadtests/stub_llm.py) behind NeedWise's own `openai_compatible`
adapter, in process and without the stack: cited streamed answers, question rewrites, the agents
pipeline (plan, findings, composed answer), the research and analytics tool loops, any other
structured-output schema, the admin provider test, and the stub's timing over a real socket.

Load tests point the stack at this stub so they measure NeedWise's own overhead
(docs/load-testing.md). If the adapter changes what it sends or expects, these tests say so.
"""

import asyncio
import importlib.util
import sys
import time
import uuid
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
import uvicorn
from starlette.applications import Starlette

from needwise.core.context import RequestContext
from needwise.evals.runner import Judgement
from needwise.modules.agents.orchestrator import (
    AgentStepEvent,
    OrchestratorDeps,
    PlanEvent,
    orchestrate,
)
from needwise.modules.agents.planning import Agent, PlanOut, plan_question
from needwise.modules.agents.research import RESEARCH_SYSTEM
from needwise.modules.analytics.agent import answer as analytics_answer
from needwise.modules.assistant.answer import Answer, AnswerEvent, answer_stream, build_request
from needwise.modules.conversations.runner import rewrite_question
from needwise.modules.meetings.extract import Extraction
from needwise.modules.model_catalog.catalog import ProviderInfo
from needwise.modules.model_catalog.service import Probe, probe
from needwise.modules.retrieval.service import Passage, RetrievalResult
from needwise.modules.tools.builtin import DraftEmail, SearchKnowledge
from needwise.providers.factory import single_model_roles
from needwise.providers.llm import (
    LLMMessage,
    LLMRequest,
    OpenAICompatibleLLM,
    TextDelta,
    ToolCall,
    ToolSpec,
)
from tests.analytics_support import SALES, LocalAnalytics, build_acme, context

STUB_FILE = Path(__file__).resolve().parents[3] / "loadtests" / "stub_llm.py"
ROLES = single_model_roles("needwise-stub")
CTX = RequestContext(
    request_id="t",
    tenant_id=uuid.uuid4(),
    user_id=uuid.uuid4(),
    org_role="member",
    principals=frozenset(),
    auth_method="service",
)
LEAVE = "Parental leave is 16 weeks, fully paid. It can start up to 4 weeks before the due date."
HOTELS = "The hotel limit in London is EUR 220 per night. Book through the travel portal."


def _load_stub() -> ModuleType:
    spec = importlib.util.spec_from_file_location("needwise_stub_llm", STUB_FILE)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up while being created
    spec.loader.exec_module(module)
    return module


stub = _load_stub()


def instant() -> Starlette:
    return stub.create_app(stub.Timing(latency_ms=0, ttft_ms=0, tokens_per_s=0))


@pytest.fixture
async def llm() -> AsyncIterator[OpenAICompatibleLLM]:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=instant())) as http:
        yield OpenAICompatibleLLM(http, ROLES, base_url="http://stub/v1")


def passage(marker: str, text: str, title: str = "Leave Policy 2026") -> Passage:
    return Passage(
        marker=marker,
        chunk_id=f"chunk-{marker}-{title}",
        document_id=f"doc-{title}",
        collection_id="c1",
        title=title,
        filename=f"{title}.md",
        heading_path=["Policy"],
        page=None,
        text=text,
        trust="official",
        sensitivity="internal",
        score=1.0,
    )


class Retriever:
    """Every search finds the same passages."""

    def __init__(self, passages: list[Passage]) -> None:
        self.passages = passages
        self.queries: list[str] = []

    async def search(self, ctx: RequestContext, query: str, **_: Any) -> RetrievalResult:
        self.queries.append(query)
        return RetrievalResult(query=query, passages=list(self.passages))


async def final_answer(events: AsyncIterator[Any]) -> tuple[Answer, list[Any]]:
    seen = [e async for e in events]
    return next(e.answer for e in seen if isinstance(e, AnswerEvent)), seen


async def test_a_streamed_answer_cites_the_sources_it_was_given(llm: OpenAICompatibleLLM) -> None:
    passages = [passage("S1", LEAVE), passage("S2", HOTELS, "Travel Policy")]

    retriever: Any = Retriever(passages)

    final, events = await final_answer(
        answer_stream(llm, retriever, CTX, "How long is parental leave?", organization="Acme")
    )

    tokens = [e for e in events if type(e).__name__ == "TokenEvent"]
    assert [c.marker for c in final.citations] == ["S1", "S2"]
    assert final.unknown_markers == []
    assert not final.abstained
    assert "Parental leave is 16 weeks, fully paid [S1]." in final.text
    assert len(tokens) >= stub.Timing().answer_tokens  # word by word, like a model streams
    assert final.usage.output_tokens == len(tokens)
    assert final.usage.input_tokens > 0


async def test_the_same_request_gets_the_same_answer(llm: OpenAICompatibleLLM) -> None:
    request = build_request("Acme", "Parental leave?", [passage("S1", LEAVE)], [])

    first = [e.text async for e in llm.stream("answer", request) if isinstance(e, TextDelta)]
    second = [e.text async for e in llm.stream("answer", request) if isinstance(e, TextDelta)]

    assert first == second


async def test_a_follow_up_is_rewritten_to_itself(llm: OpenAICompatibleLLM) -> None:
    history = [LLMMessage("user", "Parental leave?"), LLMMessage("assistant", "16 weeks.")]

    query, usage = await rewrite_question(llm, history, "And for contractors?")

    assert query == "And for contractors?"
    assert usage.output_tokens > 0


async def test_the_agents_pipeline_plans_finds_and_composes(
    llm: OpenAICompatibleLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    agents = [
        Agent("hr", "HR agent", "Parental leave, benefits and reviews.", "", ("c-hr",), (), 2),
        Agent("finance", "Finance agent", "Budgets and the hotel limits.", "", ("c-fin",), (), 2),
        Agent("sales", "Sales agent", "Customers and deals.", "", ("c-sales",), (), 2),
    ]

    async def load(*_: Any) -> list[Agent]:
        return agents

    monkeypatch.setattr("needwise.modules.agents.orchestrator.load_agents", load)
    retriever = Retriever([passage("S1", LEAVE), passage("S2", HOTELS, "Travel Policy")])
    deps = OrchestratorDeps(None, llm, None, retriever, None)  # type: ignore[arg-type]
    question = "How long is parental leave, and what is the hotel limit in London?"

    final, events = await final_answer(
        orchestrate(deps, CTX, question, organization="Acme", policy="allow")
    )

    plan = next(e.plan for e in events if isinstance(e, PlanEvent))
    done = [e for e in events if isinstance(e, AgentStepEvent) and e.status == "done"]
    assert (plan.mode, plan.routed_by, sorted(plan.agents)) == (
        "fan_out",
        "planner",
        ["finance", "hr"],
    )
    assert sorted(e.agent for e in done) == ["finance", "hr"]
    assert all(e.findings > 0 for e in done)
    assert final.citations
    assert final.unknown_markers == []


async def test_a_one_area_question_gets_one_agent(llm: OpenAICompatibleLLM) -> None:
    agents = [
        Agent("hr", "HR agent", "Parental leave and benefits.", "", ("c1",), (), 1),
        Agent("sales", "Sales agent", "Customers and deals.", "", ("c2",), (), 1),
    ]

    plan = await plan_question(llm, None, "Which deals did the sales team close?", agents)
    greeting = await plan_question(llm, None, "Hi!", agents)

    assert (plan.mode, plan.agents) == ("single", ["sales"])
    assert (greeting.mode, greeting.agents) == ("direct", [])


async def test_the_research_loop_searches_once_then_answers(llm: OpenAICompatibleLLM) -> None:
    calls: list[ToolCall] = []

    async def execute(call: ToolCall) -> tuple[str, bool]:
        calls.append(call)
        return '<result source="Leave Policy">Parental leave is 16 weeks.</result>', False

    tools = [
        ToolSpec("search_knowledge", "Search", SearchKnowledge.model_json_schema()),
        ToolSpec("draft_email", "Email", DraftEmail.model_json_schema()),
    ]
    request = LLMRequest(
        system=RESEARCH_SYSTEM.format(organization="Acme"),
        messages=[LLMMessage("user", "How long is parental leave?")],
    )

    loop = await llm.run_tools("agent", request, tools, execute)

    assert [(c.name, c.arguments) for c in calls] == [
        ("search_knowledge", {"query": "How long is parental leave?"})
    ]
    assert (loop.stopped, len(loop.usage)) == ("done", 2)
    assert loop.text


async def test_the_analytics_agent_runs_a_query_and_its_number_verifies(
    llm: OpenAICompatibleLLM, tmp_path: Path
) -> None:
    analytics = LocalAnalytics(build_acme(tmp_path))

    result = await analytics_answer(
        llm, analytics, context(SALES), "How many rows are there?", today=date(2026, 11, 12)
    )

    assert result.status == "answered", result.error
    assert [q.sql.upper().startswith("SELECT COUNT(*)") for q in result.queries] == [True]
    assert result.chart is None  # one number: nothing to plot
    assert result.main_query == "Q1"
    assert "[Q1]" in result.narrative
    assert result.verification.total == result.verification.verified == 1


@pytest.mark.parametrize("schema", [Extraction, Judgement, PlanOut])
async def test_any_other_schema_gets_a_valid_instance(
    llm: OpenAICompatibleLLM, schema: type[Any]
) -> None:
    request = LLMRequest(system="Extract.", messages=[LLMMessage("user", "A transcript.")])

    value, usage = await llm.structured("generator", request, schema)

    assert isinstance(value, schema)
    assert usage.output_tokens > 0


async def test_the_admin_provider_test_passes() -> None:
    provider = ProviderInfo(
        id="system",
        name="Deployment default",
        kind="openai_compatible",
        base_url="http://stub/v1",
        local=True,
        has_key=False,
        key_hint=None,
        enabled=True,
        system=True,
    )
    target = Probe(provider=provider, model="needwise-stub", base_url="http://stub/v1")

    result = await probe(target, transport=httpx.ASGITransport(app=instant()))

    assert (result.ok, result.error) == (True, None)


async def test_the_stub_keeps_its_timing_over_a_real_socket() -> None:
    timing = stub.Timing(latency_ms=50, ttft_ms=250, tokens_per_s=200, answer_tokens=60)
    server = uvicorn.Server(
        uvicorn.Config(stub.create_app(timing), host="127.0.0.1", port=0, log_level="warning")
    )
    serving = asyncio.create_task(server.serve())
    while not server.started:  # noqa: ASYNC110  (uvicorn has a flag, not an event)
        await asyncio.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    request = build_request("Acme", "Parental leave?", [passage("S1", LEAVE)], [])
    first: float | None = None
    count = 0
    try:
        async with httpx.AsyncClient() as http:
            llm = OpenAICompatibleLLM(http, ROLES, base_url=f"http://127.0.0.1:{port}/v1")
            started = time.perf_counter()
            async for event in llm.stream("answer", request):
                if isinstance(event, TextDelta):
                    first = first or time.perf_counter() - started
                    count += 1
            total = time.perf_counter() - started
    finally:
        server.should_exit = True
        await serving

    assert first is not None
    assert 0.3 <= first < 1.5  # latency + time to first token
    assert count == 60
    assert total >= 0.3 + (count - 1) / 200  # then 200 words per second
