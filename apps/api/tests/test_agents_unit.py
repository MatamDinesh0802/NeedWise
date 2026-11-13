"""Planning, department agents and aggregation, with scripted models and no services."""

import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import replace
from typing import Any

import pytest
from pydantic import BaseModel

from needwise.core.context import RequestContext
from needwise.modules.agents.department import AgentResult, EvidenceOut, Finding, run_agent
from needwise.modules.agents.orchestrator import (
    AgentStepEvent,
    OrchestratorDeps,
    PlanEvent,
    aggregate,
    orchestrate,
)
from needwise.modules.agents.planning import Agent, FastRouter, PlanOut, Subtask, plan_question
from needwise.modules.assistant.answer import AnswerEvent
from needwise.modules.retrieval.service import Passage, RetrievalResult
from needwise.providers.llm import Done, LLMRequest, LLMUsage, Role, StreamEvent, TextDelta

CTX = RequestContext(
    request_id="t",
    tenant_id=uuid.uuid4(),
    user_id=uuid.uuid4(),
    org_role="member",
    principals=frozenset(),
    auth_method="service",
)


def agent(key: str, collections: tuple[str, ...] = ("c1",), searches: int = 2) -> Agent:
    return Agent(key, f"{key} agent", f"{key} things", "", collections, (), searches)


def passage(chunk: str, marker: str = "S1", text: str = "text") -> Passage:
    return Passage(
        marker=marker,
        chunk_id=chunk,
        document_id=f"d-{chunk}",
        collection_id="c1",
        title=f"Doc {chunk}",
        filename=f"{chunk}.md",
        heading_path=[],
        page=None,
        text=text,
        trust="official",
        sensitivity="internal",
        score=1.0,
    )


class ScriptedLLM:
    """Returns the next scripted structured reply; streams a fixed answer."""

    external = True

    def __init__(
        self, replies: Sequence[BaseModel | Exception] = (), answer: str = "Done [S1]."
    ) -> None:
        self.replies = list(replies)
        self.answer = answer
        self.calls: list[tuple[Role, LLMRequest]] = []

    async def complete(self, role: Role, request: LLMRequest) -> tuple[str, LLMUsage]:
        raise NotImplementedError

    async def stream(self, role: Role, request: LLMRequest) -> AsyncIterator[StreamEvent]:
        self.calls.append((role, request))
        yield TextDelta(self.answer)
        yield Done(LLMUsage(model="fake", input_tokens=10, output_tokens=3))

    async def structured(self, role: Role, request: LLMRequest, schema: Any) -> Any:
        self.calls.append((role, request))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply, LLMUsage(model="fake", input_tokens=10, output_tokens=2)


class ScopedRetriever:
    def __init__(self, results: dict[str, list[Passage]]) -> None:
        self.results = results
        self.calls: list[tuple[str, Sequence[str] | None]] = []

    async def search(
        self,
        ctx: RequestContext,
        query: str,
        *,
        collection_ids: Sequence[str] | None = None,
        **_: Any,
    ) -> RetrievalResult:
        self.calls.append((query, collection_ids))
        return RetrievalResult(query=query, passages=list(self.results.get(query, [])))


async def test_the_planner_output_is_sanitized_before_anything_runs() -> None:
    agents = [agent("hr"), agent("finance")]
    llm = ScriptedLLM(
        [
            PlanOut(
                mode="fan_out",
                subtasks=[
                    Subtask(agent="hr", goal="Parental leave?"),
                    Subtask(agent="legal", goal="An agent that does not exist"),
                    Subtask(agent="hr", goal="Asked twice"),
                    Subtask(agent="finance", goal="  "),
                ],
            )
        ]
    )

    plan = await plan_question(llm, None, "q", agents)  # type: ignore[arg-type]

    assert (plan.mode, plan.agents, plan.routed_by) == ("single", ["hr"], "planner")


async def test_no_agents_or_a_direct_plan_means_the_plain_assistant() -> None:
    llm = ScriptedLLM([PlanOut(mode="direct", subtasks=[])])

    assert (await plan_question(llm, None, "hi", [])).mode == "direct"  # type: ignore[arg-type]
    assert (await plan_question(llm, None, "hi", [agent("hr"), agent("it")])).mode == "direct"  # type: ignore[arg-type]


class StubEmbeddings:
    model_id, dimensions = "stub", 3

    def __init__(self, query: list[float]) -> None:
        self.query = query

    async def embed_documents(
        self, texts: Sequence[str], *, titles: Sequence[str] | None = None
    ) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] if "hr" in t else [0.0, 1.0, 0.0] for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self.query


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ([0.9, 0.1, 0.0], "hr"),
        ([0.6, 0.55, 0.0], None),
        ([0.3, 0.1, 0.95], None),
    ],  # clear / tied / weak
)
async def test_the_fast_path_only_takes_clear_winners(
    query: list[float], expected: str | None
) -> None:
    router = FastRouter(StubEmbeddings(query), min_score=0.5, min_margin=0.15)  # type: ignore[arg-type]

    assert await router.route("q", [agent("hr"), agent("finance")]) == expected


async def test_an_agent_searches_only_its_collections_and_stops_at_its_budget() -> None:
    retriever = ScopedRetriever(
        {"goal": [passage("a", text="fact A")], "more": [passage("b", text="fact B")], "again": []}
    )
    llm = ScriptedLLM(
        [
            EvidenceOut(
                findings=[Finding(claim="A", sources=["S1"])], gaps=["B"], follow_up_query="more"
            ),
            EvidenceOut(
                findings=[
                    Finding(claim="B", sources=["S2"]),
                    Finding(claim="made up", sources=["S9"]),
                ],
                gaps=["C"],
                follow_up_query="again",
            ),
        ]
    )

    result = await run_agent(
        llm,  # type: ignore[arg-type]
        retriever,  # type: ignore[arg-type]
        CTX,
        agent("hr", ("hr-docs", "handbook"), searches=2),
        "goal",
        organization="Acme",
        policy="allow",
        local_llm=None,
    )

    assert retriever.calls == [("goal", ("hr-docs", "handbook")), ("more", ("hr-docs", "handbook"))]
    assert [p.chunk_id for p in result.passages] == ["a", "b"]
    assert [f.claim for f in result.findings] == ["B"]  # the finding citing no real source is gone
    assert result.searches == ["goal", "more"]  # "again" exceeds the budget of 2


def test_aggregation_renumbers_sources_and_translates_each_agents_markers() -> None:
    shared = passage("shared", "S2")
    hr = AgentResult(
        agent="hr",
        goal="g",
        passages=[passage("leave", "S1"), shared],
        findings=[
            Finding(claim="Leave is 16 weeks", sources=["S1"]),
            Finding(claim="x", sources=["S2"]),
        ],
    )
    finance = AgentResult(
        agent="finance",
        goal="g",
        passages=[replace(shared, marker="S1"), passage("hotel", "S2")],
        findings=[Finding(claim="Hotel cap 250", sources=["S2", "S1"])],
    )

    agg = aggregate([hr, finance])

    assert [(p.marker, p.chunk_id) for p in agg.passages] == [
        ("S1", "leave"),
        ("S2", "shared"),
        ("S3", "hotel"),
    ]
    assert agg.findings["finance"] == [("Hotel cap 250", ["S3", "S2"])]
    assert agg.findings["hr"][0] == ("Leave is 16 weeks", ["S1"])


async def test_when_every_agent_fails_the_question_is_still_answered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def two_agents(*_: Any) -> list[Agent]:
        return [agent("hr"), agent("finance")]

    monkeypatch.setattr("needwise.modules.agents.orchestrator.load_agents", two_agents)
    plan = PlanOut(
        mode="fan_out", subtasks=[Subtask(agent="hr", goal="a"), Subtask(agent="finance", goal="b")]
    )
    llm = ScriptedLLM(
        [plan, RuntimeError("down"), RuntimeError("down")], answer="Plain answer [S1]."
    )
    retriever = ScopedRetriever({"a": [passage("x")], "b": [passage("y")], "q": [passage("z")]})
    deps = OrchestratorDeps(None, llm, None, retriever, None)  # type: ignore[arg-type]

    events = [e async for e in orchestrate(deps, CTX, "q", organization="Acme", policy="allow")]

    steps = [e for e in events if isinstance(e, AgentStepEvent)]
    final = next(e.answer for e in events if isinstance(e, AnswerEvent))
    assert isinstance(events[0], PlanEvent)
    assert sorted(s.status for s in steps) == ["failed", "failed", "running", "running"]
    assert final.text == "Plain answer [S1]."
    assert [c.filename for c in final.citations] == ["z.md"]  # from the single-pipeline search


async def test_when_no_agent_finds_anything_the_plain_search_still_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A question about a collection no agent covers (e.g. contractor onboarding)."""

    async def one_agent(*_: Any) -> list[Agent]:
        return [agent("engineering", ("eng-docs",)), agent("hr")]

    monkeypatch.setattr("needwise.modules.agents.orchestrator.load_agents", one_agent)
    plan = PlanOut(mode="single", subtasks=[Subtask(agent="engineering", goal="NDA?")])
    llm = ScriptedLLM([plan], answer="Sign the NDA [S1].")
    retriever = ScopedRetriever({"q": [passage("guide")]})  # only the unscoped search finds it
    deps = OrchestratorDeps(None, llm, None, retriever, None)  # type: ignore[arg-type]

    events = [e async for e in orchestrate(deps, CTX, "q", organization="Acme", policy="allow")]

    final = next(e.answer for e in events if isinstance(e, AnswerEvent))
    assert [c.filename for c in final.citations] == ["guide.md"]
    assert retriever.calls[-1] == ("q", None)  # the fallback searches everything permitted
