"""Agent configuration without services: tools, model choice, limits, snapshots, the console,
freshness of the fast path, and the routing eval's skipping and per-agent report."""

import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from tests.fakes import FakeLLM
from tests.test_agents_unit import CTX, ScopedRetriever, StubEmbeddings, agent, passage
from workmind.evals.datasets import RoutingItem, load_routing
from workmind.evals.report import routing_section
from workmind.evals.runner import RoutingRecord, route_items, routing_by_agent
from workmind.modules.agents import department
from workmind.modules.agents.console import run_console
from workmind.modules.agents.definitions import (
    apply_fields,
    changed_fields,
    snapshot,
    tool_problem,
)
from workmind.modules.agents.department import (
    AgentTools,
    EvidenceOut,
    Finding,
    ToolOutput,
    run_agent,
)
from workmind.modules.agents.model_choice import SystemModels, is_model_id
from workmind.modules.agents.models import AgentDefinition
from workmind.modules.agents.orchestrator import AgentResult, OrchestratorDeps, aggregate
from workmind.modules.agents.planning import Agent, FastRouter, PlanOut, Subtask
from workmind.modules.tools.registry import Tool, ToolError, ToolRegistry, ToolResult
from workmind.providers.embeddings import HashEmbeddings
from workmind.providers.llm import (
    AnthropicLLM,
    LLMError,
    LLMRequest,
    OpenAICompatibleLLM,
    PinnedModel,
    RoleConfig,
)

DATASETS = Path(__file__).resolve().parents[3] / "evals" / "datasets"
# A browser session carries every scope; tools check the scope they need.
SESSION = replace(CTX, scopes=frozenset({"*"}))


# --- Tools an agent may have ------------------------------------------------------------------


class Query(BaseModel):
    question: str


def tool(name: str, side_effect: str = "read", scope: str = "assistant:ask") -> Tool:
    async def run(ctx: Any, args: Any) -> ToolResult:
        return ToolResult(f"{name} says 42")

    return Tool(name, f"{name} tool", Query, side_effect, scope, run)  # type: ignore[arg-type]


def registry(*tools: Tool) -> ToolRegistry:
    out = ToolRegistry()
    for t in tools:
        out.register(t)
    return out


def test_only_read_level_tools_besides_search_can_be_given_to_an_agent() -> None:
    tools = registry(
        tool("lookup"), tool("send_email", "external"), tool("create_task", "write"),
        tool("search_knowledge"),
    )  # fmt: skip

    assert tool_problem(tools, "lookup") is None
    assert tool_problem(tools, "nope") == ("unknown_tool", "No tool named 'nope' is registered.")
    code, message = tool_problem(tools, "send_email") or ("", "")
    assert code == "tool_not_allowed"
    assert "cannot wait for approvals" in message
    assert tool_problem(tools, "create_task")[0] == "tool_not_allowed"  # type: ignore[index]
    assert "searching is built in" in (tool_problem(tools, "search_knowledge") or ("", ""))[1]


class RecordingExecutor:
    """Stands in for execute_tool (which audits to the database): runs the handler as the
    real wrapper does, refusing anything that is not read-level."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def __call__(
        self,
        registry: ToolRegistry,
        sessions: Any,
        ctx: Any,
        name: str,
        arguments: dict[str, Any],
        *,
        caller: str = "agent",
        approved: bool = False,
    ) -> ToolResult:
        self.calls.append((name, caller))
        found = registry.get(name)
        assert found is not None
        if found.side_effect != "read":
            raise ToolError("approval_required", "needs approval")
        return await found.handler(ctx, found.validate(arguments))


def tool_agent(**changes: Any) -> Agent:
    base = agent("legal", ("legal-docs",), searches=1)
    fields = {"tools": ("lookup",), "max_tool_calls": 1, **changes}
    return Agent(**{**base.__dict__, **fields})


async def test_an_agent_calls_its_tools_within_its_limit_and_can_cite_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = RecordingExecutor()
    monkeypatch.setattr(department, "execute_tool", executor)
    tools = registry(tool("lookup"), tool("send_email", "external"), tool("other"))
    llm = FakeLLM(
        structured=[
            EvidenceOut(
                findings=[
                    Finding(claim="from the doc", sources=["S1"]),
                    Finding(claim="from the tool", sources=["T1"]),
                    Finding(claim="invented", sources=["T7"]),
                ],
                gaps=[],
                follow_up_query="",
            )
        ],
        # The model tries the tool twice, a tool the agent does not have, and a write tool.
        # A third turn would never come: the loop allows max_tool_calls + 1 turns.
        tool_turns=[
            [("lookup", {"question": "a"})],
            [
                ("lookup", {"question": "b"}),
                ("other", {"question": "c"}),
                ("send_email", {"question": "d"}),
            ],
            [("lookup", {"question": "e"})],
        ],
    )
    retriever = ScopedRetriever({"goal": [passage("a")]})

    result = await run_agent(
        llm,  # type: ignore[arg-type]
        retriever,  # type: ignore[arg-type]
        SESSION,
        tool_agent(max_output_tokens=2500),
        "goal",
        organization="Acme",
        policy="allow",
        local_llm=None,
        tools=AgentTools(tools, None),  # type: ignore[arg-type]
    )

    assert [o.name for o in llm.offered] == ["lookup"]  # only the agent's read-level tools
    assert executor.calls == [("lookup", "agent:legal")]  # one call: its limit is 1
    assert [(c.tool, c.ok, c.error) for c in result.tool_calls] == [
        ("lookup", True, None),
        ("lookup", False, "limit_reached"),
        ("other", False, "not_offered"),
        ("send_email", False, "not_offered"),
    ]
    assert result.tool_outputs == [ToolOutput("T1", "lookup", "lookup says 42")]
    assert [f.claim for f in result.findings] == ["from the doc", "from the tool"]
    extraction = llm.requests[-1][1]
    assert '<tool_result marker="T1" tool="lookup">' in extraction.messages[0].content
    assert {r.max_tokens for _, r in llm.requests} == {2500}  # the output cap applies


async def test_an_agent_without_tools_or_without_tool_calls_is_offered_none() -> None:
    tools = registry(tool("lookup"))
    llm = FakeLLM(structured=[EvidenceOut(findings=[], gaps=[], follow_up_query="")])
    retriever = ScopedRetriever({"goal": [passage("a")]})

    await run_agent(
        llm,  # type: ignore[arg-type]
        retriever,  # type: ignore[arg-type]
        SESSION,
        tool_agent(max_tool_calls=0),
        "goal",
        organization="Acme",
        policy="allow",
        local_llm=None,
        tools=AgentTools(tools, None),  # type: ignore[arg-type]
    )

    assert llm.roles() == ["agent"]  # one extraction, no tool loop


def test_aggregation_renumbers_tool_outputs_across_agents() -> None:
    legal = AgentResult(
        agent="legal",
        goal="g",
        passages=[passage("p", "S1")],
        tool_outputs=[ToolOutput("T1", "lookup", "x")],
        findings=[Finding(claim="from tool", sources=["T1"])],
    )
    finance = AgentResult(
        agent="finance",
        goal="g",
        tool_outputs=[ToolOutput("T1", "query_data", "y")],
        findings=[Finding(claim="figure", sources=["T1"])],
    )

    agg = aggregate([legal, finance])

    assert [(a, o.marker, o.tool) for a, o in agg.tool_outputs] == [
        ("legal", "T1", "lookup"),
        ("finance", "T2", "query_data"),
    ]
    assert agg.findings["finance"] == [("figure", ["T2"])]


# --- Model choice -----------------------------------------------------------------------------


def test_model_ids_name_a_provider_and_a_model() -> None:
    assert is_model_id("system:claude-sonnet-5-5")
    assert is_model_id(f"{uuid.uuid4()}:llama3.3:70b")
    assert not is_model_id("claude-sonnet-5-5")
    assert not is_model_id("openai:gpt")  # a provider is "system" or a provider id
    assert not is_model_id("system:")
    assert not is_model_id("system:two words")


async def test_a_system_model_pins_the_deployment_provider_and_others_fail_closed() -> None:
    base = FakeLLM(external=False)
    resolver = SystemModels()

    assert await resolver.agent_llm(CTX.tenant_id, None, base) is base  # type: ignore[arg-type]
    pinned = await resolver.agent_llm(CTX.tenant_id, "system:claude-x", base)  # type: ignore[arg-type]
    assert isinstance(pinned, PinnedModel)
    assert pinned.external is False  # sensitivity routing sees the real provider
    await pinned.complete("agent", LLMRequest(system="s", messages=[]))
    assert base.requests[-1][1].model == "claude-x"
    with pytest.raises(LLMError, match="not in this deployment's model catalog"):
        await resolver.agent_llm(CTX.tenant_id, f"{uuid.uuid4()}:llama3", base)  # type: ignore[arg-type]


def test_providers_send_the_request_model_instead_of_the_roles() -> None:
    roles = {"agent": RoleConfig("claude-default", "medium")}
    anthropic = AnthropicLLM(roles, api_key="test-key")  # type: ignore[arg-type]
    local = OpenAICompatibleLLM(None, roles, base_url="http://x/v1")  # type: ignore[arg-type]
    plain = LLMRequest(system="s", messages=[])
    pinned = LLMRequest(system="s", messages=[], model="claude-sonnet-5-5")

    assert anthropic._params("agent", plain)["model"] == "claude-default"  # pyright: ignore[reportPrivateUsage]
    params = anthropic._params("agent", pinned)  # pyright: ignore[reportPrivateUsage]
    assert params["model"] == "claude-sonnet-5-5"
    assert params["fallbacks"] == "default"  # decided by the model actually used
    assert local._body("agent", pinned)["model"] == "claude-sonnet-5-5"  # pyright: ignore[reportPrivateUsage]


async def test_a_department_agent_with_a_model_runs_on_it() -> None:
    from workmind.modules.agents.orchestrator import run_agent_bounded

    llm = FakeLLM(structured=[EvidenceOut(findings=[], gaps=[], follow_up_query="")])
    deps = OrchestratorDeps(None, llm, None, ScopedRetriever({"g": [passage("a")]}), None)  # type: ignore[arg-type]

    await run_agent_bounded(
        deps, CTX, tool_agent(model="system:claude-y", tools=()), "g",
        organization="Acme", policy="allow",
    )  # fmt: skip
    failed = await run_agent_bounded(
        deps, CTX, tool_agent(model=f"{uuid.uuid4()}:x", tools=()), "g",
        organization="Acme", policy="allow",
    )  # fmt: skip

    assert [r.model for _, r in llm.requests] == ["claude-y"]
    assert failed.error is not None
    assert failed.error.startswith("model unavailable")


# --- Snapshots ----------------------------------------------------------------------------------


def definition(**fields: Any) -> AgentDefinition:
    d = AgentDefinition(
        tenant_id=uuid.uuid4(),
        key="legal",
        name="Legal agent",
        description="Contracts and NDAs.",
        instructions="",
        collection_keys=["legal-contracts"],
        examples=["Who signs?"],
        tools=[],
        model=None,
        max_searches=2,
        max_tool_calls=0,
        max_output_tokens=6000,
        enabled=True,
    )
    apply_fields(d, fields)
    return d


def test_a_snapshot_round_trips_and_names_what_changed() -> None:
    before = snapshot(definition())
    edited = definition(examples=["Who signs?", "NDA?"], model="system:claude-x")
    restored = definition(**snapshot(edited))

    assert snapshot(restored) == snapshot(edited)
    assert changed_fields(before, snapshot(edited)) == ["examples", "model"]
    assert set(before) == {
        "key", "name", "description", "instructions", "collections", "examples", "tools",
        "model", "max_searches", "max_tool_calls", "max_output_tokens", "enabled",
    }  # fmt: skip


def test_an_older_snapshot_keeps_fields_it_does_not_have() -> None:
    d = definition(tools=["lookup"], max_tool_calls=2)
    apply_fields(d, {"name": "Legal", "description": "Old text."})  # a pre-tools snapshot

    assert (d.name, d.tools, d.max_tool_calls) == ("Legal", ["lookup"], 2)


# --- Freshness of the fast path -----------------------------------------------------------------


async def test_the_fast_path_embeds_an_edited_profile_on_the_next_question() -> None:
    router = FastRouter(HashEmbeddings(256), min_score=0.3, min_margin=0.15)
    hr = Agent(
        "hr", "HR agent", "Leave, holidays, probation and remote work.", "", ("c",),
        ("How many weeks of parental leave do we get?",), 2,
    )  # fmt: skip
    legal = Agent("legal", "Legal agent", "Robot maintenance.", "", ("c",), (), 2)
    question = "Who may sign a supplier contract worth 300,000 euros?"

    before = await router.route(question, [hr, legal])
    edited = Agent(
        "legal", "Legal agent", "Contracts: who may sign them, NDAs, supplier contract reviews.",
        "", ("c",), ("Who may sign a supplier contract?",), 2,
    )  # fmt: skip
    after = await router.route(question, [hr, edited])

    assert before is None
    assert after == "legal"
    assert await router.route(question, [hr]) is None  # disabled: not passed in, not routed


async def test_the_profile_cache_is_bounded() -> None:
    router = FastRouter(StubEmbeddings([1.0, 0.0, 0.0]), max_profiles=3)  # type: ignore[arg-type]
    for n in range(5):
        await router.scores("q", [agent(f"hr{n}"), agent("finance")])

    assert len(router._profiles) == 3  # pyright: ignore[reportPrivateUsage]
    assert ("finance", agent("finance").profile) in router._profiles  # pyright: ignore[reportPrivateUsage]


# --- The test console ---------------------------------------------------------------------------


async def test_the_console_routes_with_the_draft_in_place_and_answers_with_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved = [agent("hr"), Agent("legal", "Legal agent", "old text", "", ("c1",), (), 2)]

    async def enabled(*_: Any) -> list[Agent]:
        return saved

    monkeypatch.setattr("workmind.modules.agents.console.load_agents", enabled)
    draft = Agent("legal", "Legal (draft)", "Contracts and NDAs", "", ("legal-docs",), (), 1)
    llm = FakeLLM(
        "The CFO signs above 250,000 euros [S1].",
        structured=[
            PlanOut(mode="single", subtasks=[Subtask(agent="legal", goal="Who signs?")]),
            EvidenceOut(
                findings=[Finding(claim="CFO above 250k", sources=["S1"])],
                gaps=[],
                follow_up_query="",
            ),
        ],
    )
    retriever = ScopedRetriever({"Who signs?": [passage("matrix")]})
    deps = OrchestratorDeps(None, llm, None, retriever, None)  # type: ignore[arg-type]

    run = await run_console(deps, CTX, "who signs", draft, organization="Acme", policy="allow")

    planner = llm.requests[0][1].system
    assert '"legal": Contracts and NDAs' in planner
    assert "old text" not in planner
    assert (run.plan.agents, run.names, run.routed_to_tested) == (
        ["legal"],
        {"legal": "Legal (draft)"},
        True,
    )
    assert retriever.calls == [("Who signs?", ("legal-docs",))]  # the draft's collections
    assert run.answer is not None
    assert [c.filename for c in run.answer.citations] == ["matrix.md"]
    assert llm.roles() == ["router", "agent", "answer"]
    assert sum(u.input_tokens for u in run.usage) == 30 + 30 + 100  # every call counted


async def test_the_console_answers_with_the_draft_even_when_routing_misses_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def enabled(*_: Any) -> list[Agent]:
        return [agent("hr"), agent("finance")]

    monkeypatch.setattr("workmind.modules.agents.console.load_agents", enabled)
    draft = agent("legal", ("legal-docs",))
    llm = FakeLLM(
        structured=[
            PlanOut(mode="single", subtasks=[Subtask(agent="hr", goal="Probation length?")]),
            EvidenceOut(findings=[], gaps=["nothing"], follow_up_query=""),
        ]
    )
    retriever = ScopedRetriever({"probation?": [passage("x")]})
    deps = OrchestratorDeps(None, llm, None, retriever, None)  # type: ignore[arg-type]

    run = await run_console(deps, CTX, "probation?", draft, organization="Acme", policy="allow")

    assert run.plan.agents == ["hr"]
    assert run.routed_to_tested is False
    assert run.agent is not None
    assert run.agent.goal == "probation?"


# --- The routing eval ---------------------------------------------------------------------------


async def test_routing_items_for_a_missing_agent_are_skipped_not_failed() -> None:
    items = [
        RoutingItem(id="a", question="leave?", mode="single", agents=["hr"]),
        RoutingItem(id="b", question="NDA?", mode="single", agents=["legal"]),
        RoutingItem(id="c", question="contract and leave?", mode="fan_out", agents=["hr", "legal"]),
        RoutingItem(
            id="d", question="probation?", mode="single", agents=["hr"], not_agents=["legal"]
        ),
    ]
    llm = FakeLLM(
        structured_script=lambda request: PlanOut(
            mode="single", subtasks=[Subtask(agent="hr", goal="x")]
        )
    )

    records = await route_items(llm, None, [agent("hr"), agent("finance")], items)  # type: ignore[arg-type]

    assert [(r.id, r.skipped) for r in records] == [
        ("a", None),
        ("b", "agent missing: legal"),
        ("c", "agent missing: legal"),
        ("d", None),
    ]
    assert [r.correct for r in records] == [True, False, False, True]
    assert len(llm.requests) == 2  # skipped items never reach the planner
    report = "\n".join(routing_section(records))
    assert "Exact agent set: **2/2**" in report
    assert "- b: skipped (agent missing: legal)" in report
    assert "Near misses kept away from the agent they sound like: **1/1**" in report
    assert "| hr | 2 | 2/2 | 2/2 | 0 |" in report


def test_per_agent_accuracy_counts_misses_and_false_positives() -> None:
    def record(expected: list[str], got: list[str]) -> RoutingRecord:
        return RoutingRecord("x", "single", expected, "single", got, "planner", 1.0)

    stats = routing_by_agent(
        [record(["legal"], ["legal"]), record(["legal"], ["hr"]), record(["hr"], ["legal"])]
    )

    assert stats["legal"] == {"expected": 2, "exact": 1, "picked": 1, "false_positives": 1}
    assert stats["hr"] == {"expected": 1, "exact": 0, "picked": 0, "false_positives": 1}


def test_a_routing_item_must_match_its_mode() -> None:
    with pytest.raises(ValueError, match="needs 1 agent"):
        RoutingItem(id="x", question="q", mode="single", agents=["hr", "legal"])
    with pytest.raises(ValueError, match="both expected and excluded"):
        RoutingItem(id="x", question="q", mode="single", agents=["hr"], not_agents=["hr"])


def test_the_routing_dataset_covers_legal_and_the_config_only_agents() -> None:
    items = load_routing(DATASETS)
    ids = [i.id for i in items]
    single = [i for i in items if i.mode == "single"]
    legal_single = [i for i in single if i.agents == ["legal"]]
    legal_multi = [i for i in items if i.mode == "fan_out" and "legal" in i.agents]
    near_misses = [i for i in items if "legal" in i.not_agents]

    assert len(ids) == len(set(ids))
    assert len(legal_single) >= 8
    assert len(legal_multi) >= 3
    assert {a for i in legal_multi for a in i.agents} >= {"legal", "finance", "sales", "hr"}
    assert len(near_misses) >= 2
    for key in ("support", "sales", "marketing"):  # created by configuration only
        assert sum(i.agents == [key] for i in single) >= 3, key


def test_support_sales_and_marketing_are_configuration_only() -> None:
    import yaml

    company = yaml.safe_load((DATASETS.parents[1] / "seed" / "acme" / "company.yaml").read_text())
    keys = {a["key"] for a in company["agents"]}

    assert {"support", "sales", "marketing"} <= keys
    assert "legal" not in keys  # created in the UI (docs/agents.md), not seeded
    assert any(c["key"] == "legal-contracts" for c in company["collections"])
