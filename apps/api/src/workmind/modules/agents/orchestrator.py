"""Multi-agent answers (ARCHITECTURE section 12): plan -> agents in parallel -> aggregate and
validate evidence -> compose one cited answer, streamed.

Plain asyncio rather than a graph framework (ADR 0009). Every agent acts as the asking user, so
the orchestration can never see more than a single search would. A question the planner sends
"direct", or one where every agent fails, is answered by the single-pipeline assistant.
"""

import asyncio
import html
from collections.abc import AsyncGenerator, Sequence
from dataclasses import dataclass, field, replace
from typing import Literal

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from workmind.core.context import RequestContext
from workmind.modules.agents.department import AgentResult, run_agent
from workmind.modules.agents.planning import Agent, FastRouter, Plan, load_agents, plan_question
from workmind.modules.assistant.answer import (
    ABSTAIN,
    SYSTEM,
    WITHHELD,
    Answer,
    AnswerEvent,
    RestrictedPolicy,
    SourcesEvent,
    StreamEvent,
    TokenEvent,
    answer_stream,
    finish,
    render_sources,
    route,
)
from workmind.modules.retrieval.service import Passage, Retriever
from workmind.providers.llm import LLMMessage, LLMProvider, LLMRequest, LLMUsage, TextDelta

log = structlog.get_logger("workmind.agents")
AGENT_TIMEOUT_S = 90.0


@dataclass(frozen=True)
class PlanEvent:
    plan: Plan
    names: dict[str, str] = field(default_factory=dict[str, str])  # agent key -> display name


@dataclass(frozen=True)
class AgentStepEvent:
    agent: str
    status: Literal["running", "done", "failed"]
    label: str
    name: str = ""  # the agent's display name, as configured
    findings: int = 0
    sources: int = 0
    seconds: float = 0.0


OrchestratorEvent = StreamEvent | PlanEvent | AgentStepEvent


@dataclass
class OrchestratorDeps:
    sessions: async_sessionmaker[AsyncSession]
    llm: LLMProvider
    local_llm: LLMProvider | None
    retriever: Retriever
    router: FastRouter | None


@dataclass
class Aggregate:
    passages: list[Passage] = field(default_factory=list[Passage])
    findings: dict[str, list[tuple[str, list[str]]]] = field(
        default_factory=dict[str, list[tuple[str, list[str]]]]
    )
    gaps: dict[str, list[str]] = field(default_factory=dict[str, list[str]])
    withheld: int = 0


def aggregate(results: Sequence[AgentResult]) -> Aggregate:
    """Merge every agent's sources into one numbering (S1..Sn), translating each finding's
    markers, and drop findings that no longer point at a known source (the validator)."""
    out = Aggregate()
    global_marker: dict[str, str] = {}
    for result in results:
        local: dict[str, str] = {}
        for passage in result.passages:
            if passage.chunk_id not in global_marker:
                marker = f"S{len(out.passages) + 1}"
                global_marker[passage.chunk_id] = marker
                out.passages.append(replace(passage, marker=marker))
            local[passage.marker] = global_marker[passage.chunk_id]
        out.findings[result.agent] = [
            (f.claim, [local[m] for m in f.sources if m in local])
            for f in result.findings
            if any(m in local for m in f.sources)
        ]
        out.gaps[result.agent] = result.gaps
        out.withheld = max(out.withheld, result.withheld)
    return out


COMPOSE_INSTRUCTIONS = """

Specialist agents searched different areas of the company's documents and extracted the \
findings below, citing the same source markers. Use them to organise the answer, but check every \
statement against the sources and cite the sources' markers. When an agent reports a gap, say \
plainly what could not be found."""


def compose_request(
    organization: str,
    question: str,
    agg: Aggregate,
    history: Sequence[LLMMessage],
    summary: str,
) -> LLMRequest:
    system = SYSTEM.format(organization=organization, abstain=ABSTAIN) + COMPOSE_INSTRUCTIONS
    if summary:
        system += (
            "\n\nEarlier in this conversation (a summary, not a source):\n"
            f"<conversation_summary>\n{summary}\n</conversation_summary>"
        )
    blocks: list[str] = []
    for agent, findings in agg.findings.items():
        lines = [f"- {html.escape(claim)} [{', '.join(markers)}]" for claim, markers in findings]
        lines += [f"- gap: {html.escape(gap)}" for gap in agg.gaps.get(agent, [])]
        blocks.append(
            f'<findings agent="{agent}">\n' + "\n".join(lines or ["- none"]) + "\n</findings>"
        )
    content = (
        f"<sources>\n{render_sources(agg.passages)}\n</sources>\n\n"
        + "\n\n".join(blocks)
        + f"\n\nQuestion: {question}"
    )
    return LLMRequest(system=system, messages=[*history, LLMMessage("user", content)])


async def orchestrate(
    deps: OrchestratorDeps,
    ctx: RequestContext,
    question: str,
    *,
    organization: str,
    history: Sequence[LLMMessage] = (),
    summary: str = "",
    search_query: str | None = None,
    policy: RestrictedPolicy = "local_only",
    agents_only_for_fan_out: bool = False,
) -> AsyncGenerator[OrchestratorEvent]:
    """`agents_only_for_fan_out` is the "auto" mode: one-area questions take the faster single
    pipeline, and agents run only when the plan needs several areas (see ADR 0009)."""
    standalone = search_query or question
    agents = {a.key: a for a in await load_agents(deps.sessions, ctx.tenant_id)}
    plan = await plan_question(deps.llm, deps.router, standalone, list(agents.values()))
    yield PlanEvent(plan, {key: agents[key].name for key in plan.agents if key in agents})

    async def single_pipeline() -> AsyncGenerator[StreamEvent]:
        async for event in answer_stream(
            deps.llm,
            deps.retriever,
            ctx,
            question,
            organization=organization,
            history=history,
            summary=summary,
            search_query=search_query,
            policy=policy,
            local_llm=deps.local_llm,
        ):
            yield event

    if plan.mode == "direct" or (agents_only_for_fan_out and plan.mode == "single"):
        async for event in single_pipeline():
            yield _with_plan_usage(event, plan)
        return

    results: list[AgentResult] = []
    for subtask in plan.subtasks:
        yield AgentStepEvent(subtask.agent, "running", subtask.goal, agents[subtask.agent].name)
    tasks = {
        asyncio.create_task(
            _bounded(deps, ctx, agents[s.agent], s.goal, organization=organization, policy=policy)
        ): s.agent
        for s in plan.subtasks
    }
    for finished in asyncio.as_completed(tasks):
        result = await finished
        results.append(result)
        name = agents[result.agent].name
        if result.error:
            yield AgentStepEvent(result.agent, "failed", result.error, name, seconds=result.seconds)
        else:
            yield AgentStepEvent(
                result.agent,
                "done",
                result.goal,
                name,
                findings=len(result.findings),
                sources=len(result.passages),
                seconds=result.seconds,
            )

    usable = [r for r in results if not r.error]
    # Graceful degradation: if every specialist failed, or none found anything in its area
    # (no agent covers every collection), answer with the plain search over everything the
    # person may read. Same permissions either way.
    if not usable or not any(r.passages or r.withheld for r in usable):
        async for event in single_pipeline():
            yield _with_plan_usage(event, plan, results)
        return

    agg = aggregate(usable)
    chosen = route(agg.passages, policy=policy, llm=deps.llm, local_llm=deps.local_llm)
    agg.passages = chosen.passages
    withheld = max(agg.withheld, chosen.withheld)
    yield SourcesEvent(agg.passages, withheld)
    if not agg.passages:
        text = ABSTAIN + (" " + WITHHELD.format(count=withheld) if withheld else "")
        yield TokenEvent(text)
        yield AnswerEvent(
            _merge_usage(
                Answer(text=text, passages=[], abstained=True, withheld=withheld), plan, results
            )
        )
        return

    parts: list[str] = []
    usage = LLMUsage()
    request = compose_request(organization, question, agg, history, summary)
    async for event in chosen.llm.stream("answer", request):
        if isinstance(event, TextDelta):
            parts.append(event.text)
            yield TokenEvent(event.text)
        else:
            usage = event.usage
    answer = finish("".join(parts), agg.passages, usage)
    answer.withheld = withheld
    answer.local_model = not chosen.llm.external
    yield AnswerEvent(_merge_usage(answer, plan, results))


async def _bounded(
    deps: OrchestratorDeps,
    ctx: RequestContext,
    agent: Agent,
    goal: str,
    *,
    organization: str,
    policy: RestrictedPolicy,
) -> AgentResult:
    """Budgets and failure isolation: one slow or broken agent never sinks the answer."""
    try:
        async with asyncio.timeout(AGENT_TIMEOUT_S):
            return await run_agent(
                deps.llm,
                deps.retriever,
                ctx,
                agent,
                goal,
                organization=organization,
                policy=policy,
                local_llm=deps.local_llm,
            )
    except TimeoutError:
        return AgentResult(agent=agent.key, goal=goal, error="timed out")
    except Exception as exc:
        log.warning("agent_failed", agent=agent.key, error=str(exc)[:300])
        return AgentResult(agent=agent.key, goal=goal, error="failed")


def _merge_usage(answer: Answer, plan: Plan, results: Sequence[AgentResult] = ()) -> Answer:
    """The answer's usage covers the whole run: planning, every agent call, composing.
    `model` stays the composer's, so per-model pricing applies to the larger share."""
    extra = [plan.usage, *(u for r in results for u in r.usage)]
    answer.extra_usage = [u for u in extra if u.input_tokens or u.output_tokens]
    return answer


def _with_plan_usage(
    event: StreamEvent, plan: Plan, results: Sequence[AgentResult] = ()
) -> StreamEvent:
    if isinstance(event, AnswerEvent):
        return AnswerEvent(_merge_usage(event.answer, plan, results))
    return event
