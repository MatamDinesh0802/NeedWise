"""The agent test console (docs/agents.md): try a definition, saved or not, before people
depend on it.

Routing runs the real planner (and fast path) over the tenant's enabled agents with the draft
put in place of the saved agent of the same key, or added. The answer runs the department
agent with the draft definition, as the administrator asking: their permissions, narrowed to
the draft's collections, exactly as it would run for anyone else. Nothing is stored as a
conversation.
"""

from dataclasses import dataclass, field

from workmind.core.context import RequestContext
from workmind.modules.agents.department import AgentResult
from workmind.modules.agents.orchestrator import (
    OrchestratorDeps,
    compose_answer,
    run_agent_bounded,
)
from workmind.modules.agents.planning import Agent, Plan, load_agents, plan_question
from workmind.modules.assistant.answer import Answer, AnswerEvent, RestrictedPolicy
from workmind.providers.llm import LLMUsage


@dataclass
class ConsoleRun:
    plan: Plan
    names: dict[str, str]  # agent key -> display name, for every routed agent
    agent: AgentResult | None = None  # the tested agent's own run
    answer: Answer | None = None
    usage: list[LLMUsage] = field(default_factory=list[LLMUsage])  # every model call

    @property
    def routed_to_tested(self) -> bool:
        return self.agent is not None and self.agent.agent in self.plan.agents


async def run_console(
    deps: OrchestratorDeps,
    ctx: RequestContext,
    question: str,
    tested: Agent | None,
    *,
    organization: str,
    policy: RestrictedPolicy,
) -> ConsoleRun:
    """`tested` is the draft (or a saved agent, enabled or not); None only shows routing."""
    agents = await load_agents(deps.sessions, ctx.tenant_id)
    if tested is not None:
        agents = sorted([a for a in agents if a.key != tested.key] + [tested], key=lambda a: a.key)
    plan = await plan_question(deps.llm, deps.router, question, agents)
    names = {a.key: a.name for a in agents if a.key in plan.agents}
    run = ConsoleRun(plan, names, usage=[plan.usage])
    if tested is None:
        return run

    # The planner's goal for this agent when it was picked; the question itself otherwise,
    # so an administrator also sees what the agent would say when routing misses it.
    goal = next((s.goal for s in plan.subtasks if s.agent == tested.key), question)
    run.agent = await run_agent_bounded(
        deps, ctx, tested, goal, organization=organization, policy=policy
    )
    run.usage += run.agent.usage
    if run.agent.error:
        return run
    async for event in compose_answer(
        deps, question, [run.agent], organization=organization, policy=policy
    ):
        if isinstance(event, AnswerEvent):
            run.answer = event.answer
            run.usage.append(event.answer.usage)
    return run
