"""Which agents should handle a question (ARCHITECTURE 12.1-12.2).

Two routes to a plan:
- **Fast path:** the question is embedded and compared with each agent's profile (description
  and example questions). A clear winner gets the question without any LLM call.
- **Planner:** otherwise an LLM writes a plan as structured output: answer directly, hand the
  question to one agent, or split it into self-contained subtasks for several agents.
"""

import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from workmind.core.tenancy import tenant_transaction
from workmind.modules.agents.models import AgentDefinition
from workmind.modules.knowledge.models import Collection
from workmind.providers.embeddings import EmbeddingProvider
from workmind.providers.llm import LLMMessage, LLMProvider, LLMRequest, LLMUsage

MAX_SUBTASKS = 4
Mode = Literal["direct", "single", "fan_out"]
RoutedBy = Literal["fast_path", "planner", "fallback"]


@dataclass(frozen=True)
class Agent:
    key: str
    name: str
    description: str
    instructions: str
    collection_ids: tuple[str, ...]
    examples: tuple[str, ...]
    max_searches: int

    @property
    def profile(self) -> str:
        return f"{self.name}: {self.description} Examples: {' '.join(self.examples)}"


async def load_agents(
    sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> list[Agent]:
    async with tenant_transaction(sessions, tenant_id) as session:
        definitions = (
            await session.scalars(
                select(AgentDefinition)
                .where(AgentDefinition.enabled.is_(True))
                .order_by(AgentDefinition.key)
            )
        ).all()
        collections = {c.key: str(c.id) for c in await session.scalars(select(Collection))}
    return [
        Agent(
            key=d.key,
            name=d.name,
            description=d.description,
            instructions=d.instructions,
            collection_ids=tuple(collections[k] for k in d.collection_keys if k in collections),
            examples=tuple(d.examples),
            max_searches=d.max_searches,
        )
        for d in definitions
    ]


class Subtask(BaseModel):
    agent: str = Field(description="key of one of the listed agents")
    goal: str = Field(description="a self-contained question for that agent")


class PlanOut(BaseModel):
    mode: Mode
    subtasks: list[Subtask]


@dataclass
class Plan:
    mode: Mode
    subtasks: list[Subtask]
    routed_by: RoutedBy
    usage: LLMUsage = field(default_factory=LLMUsage)

    @property
    def agents(self) -> list[str]:
        return [s.agent for s in self.subtasks]


PLANNER_SYSTEM = """You plan how a company assistant answers an employee's question. Specialist \
agents each search one area of the company's documents:

{agents}

Choose a mode:
- "direct": the question is general, chit-chat, or not tied to any area above. No subtasks.
- "single": one agent's area covers the whole question. One subtask.
- "fan_out": the question needs facts from two or more areas. One subtask per area (at most \
{max_subtasks}), each a self-contained question that agent can search for.

Write each goal so it makes sense without the original question (keep names, numbers and \
identifiers). Use only the agent keys listed. Never plan to get around access rules: agents can \
only see what the employee may see."""


class FastRouter:
    """Embedding similarity between a question and each agent's profile."""

    def __init__(
        self, embeddings: EmbeddingProvider, *, min_score: float = 0.3, min_margin: float = 0.15
    ) -> None:
        self._embeddings = embeddings
        self.min_score = min_score
        self.min_margin = min_margin
        self._profiles: dict[tuple[str, str], list[float]] = {}

    async def scores(self, question: str, agents: Sequence[Agent]) -> list[tuple[str, float]]:
        missing = [a for a in agents if (a.key, a.profile) not in self._profiles]
        if missing:
            vectors = await self._embeddings.embed_documents(
                [a.profile for a in missing], titles=[a.name for a in missing]
            )
            self._profiles.update(
                {(a.key, a.profile): v for a, v in zip(missing, vectors, strict=True)}
            )
        query = await self._embeddings.embed_query(question)
        scored = [(a.key, _cosine(query, self._profiles[(a.key, a.profile)])) for a in agents]
        return sorted(scored, key=lambda pair: -pair[1])

    async def route(self, question: str, agents: Sequence[Agent]) -> str | None:
        if len(agents) < 2:
            return None
        ranked = await self.scores(question, agents)
        (best, top), (_, second) = ranked[0], ranked[1]
        return best if top >= self.min_score and top - second >= self.min_margin else None


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


async def plan_question(
    llm: LLMProvider,
    router: FastRouter | None,
    question: str,
    agents: Sequence[Agent],
) -> Plan:
    if not agents:
        return Plan("direct", [], "fallback")
    if router is not None and (key := await router.route(question, agents)):
        return Plan("single", [Subtask(agent=key, goal=question)], "fast_path")
    listing = "\n".join(f'- "{a.key}": {a.description}' for a in agents)
    request = LLMRequest(
        system=PLANNER_SYSTEM.format(agents=listing, max_subtasks=MAX_SUBTASKS),
        messages=[LLMMessage("user", f"Question: {question}")],
        max_tokens=4000,
    )
    out, usage = await llm.structured("router", request, PlanOut)
    known = {a.key for a in agents}
    seen: set[str] = set()
    subtasks: list[Subtask] = []
    for subtask in out.subtasks:  # keep valid agents, once each, within the budget
        if subtask.agent in known and subtask.agent not in seen and subtask.goal.strip():
            seen.add(subtask.agent)
            subtasks.append(subtask)
    subtasks = subtasks[:MAX_SUBTASKS]
    if out.mode == "direct" or not subtasks:
        return Plan("direct", [], "planner", usage)
    mode: Mode = "fan_out" if len(subtasks) > 1 else "single"
    return Plan(mode, subtasks, "planner", usage)
