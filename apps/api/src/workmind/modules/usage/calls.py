"""Who a model call is for, and what it used (docs/usage.md).

Every entry point that leads to model calls says what they are for:

    with attribute("chat", user_id=ctx.user_id, ref=("message", answer_id)):
        ...  # every model call in here (and in tasks started in here) is attributed

The tenant resolver (modules/model_catalog/resolver.py) reads the attribution when a call
starts, asks the meter whether a budget still allows it, and hands the finished call to the
meter, which records one usage event. Nothing here touches the database: this module is a leaf
that the resolver and the meter both import.

A context variable carries the attribution, so it follows the request into the tasks it starts
(asyncio copies the context when a task is created). Set it in a coroutine or a plain `with`
block, never across a `yield` of an async generator: the generator's consumer would see it.
"""

import uuid
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from typing import Final, Literal, Protocol, get_args

from workmind.providers.llm import LLMUsage

Feature = Literal[
    "chat", "agents", "research", "workflow", "meetings", "analytics", "agent_test", "eval"
]
# A call made outside every known entry point is still recorded, under this name (and logged).
UNATTRIBUTED: Final = "other"
FEATURES: Final[tuple[str, ...]] = (*get_args(Feature), UNATTRIBUTED)
CallStatus = Literal["ok", "failed", "cancelled"]


@dataclass(frozen=True)
class Attribution:
    """What the model calls made right now are for.

    `user_id` is the person the calls act for (None: evaluations). `enforce` is False where
    budgets are recorded but never block (evaluations an operator runs from the command line).
    """

    feature: str
    user_id: uuid.UUID | None = None
    agent_key: str | None = None
    ref_type: str | None = None
    ref_id: str | None = None
    enforce: bool = True


_current: ContextVar[Attribution | None] = ContextVar("workmind_usage_attribution", default=None)


def current() -> Attribution | None:
    return _current.get()


@contextmanager
def attribute(
    feature: Feature,
    *,
    user_id: uuid.UUID | None,
    ref: tuple[str, object] | None = None,
    agent_key: str | None = None,
    enforce: bool = True,
) -> Generator[Attribution]:
    """Attributes the model calls made inside the block (and in tasks started inside it)."""
    attribution = Attribution(
        feature=feature,
        user_id=user_id,
        agent_key=agent_key,
        ref_type=ref[0] if ref else None,
        ref_id=str(ref[1]) if ref else None,
        enforce=enforce,
    )
    token = _current.set(attribution)
    try:
        yield attribution
    finally:
        _current.reset(token)


@contextmanager
def refine(
    *,
    agent_key: str | None = None,
    ref: tuple[str, object] | None = None,
    feature: Feature | None = None,
) -> Generator[None]:
    """Narrows the current attribution inside the block: a department agent's key, the record
    the calls belong to (known only once it exists), or a more specific feature. Without a
    current attribution there is nothing to narrow and the block runs unchanged."""
    base = _current.get()
    if base is None:
        yield
        return
    changes: dict[str, object] = {}
    if agent_key is not None:
        changes["agent_key"] = agent_key
    if ref is not None:
        changes["ref_type"], changes["ref_id"] = ref[0], str(ref[1])
    if feature is not None:
        changes["feature"] = feature
    token = _current.set(replace(base, **changes))  # pyright: ignore[reportArgumentType]
    try:
        yield
    finally:
        _current.reset(token)


@dataclass
class ModelCall:
    """One call of the tenant resolver (complete, stream, structured or run_tools), as it
    ended. `usages` has one entry per model response (a tool loop has several); it is empty
    when the call failed before the provider reported anything."""

    method: str
    role: str
    model_id: str  # the catalog id, e.g. "system:claude-sonnet-5-5"
    model: str  # the model name the provider reported, else the catalog model's name
    provider: str  # "anthropic" or "openai_compatible"
    local: bool  # requests stay inside the organization's infrastructure
    status: CallStatus
    latency_ms: int
    usages: list[LLMUsage] = field(default_factory=list[LLMUsage])
    price_per_mtok: tuple[float, float] | None = None  # the tenant catalog's price, if any

    @property
    def input_tokens(self) -> int:
        return sum(u.input_tokens for u in self.usages)

    @property
    def output_tokens(self) -> int:
        return sum(u.output_tokens for u in self.usages)


class CallMeter(Protocol):
    """What the tenant resolver needs from usage accounting (modules/usage/meter.py)."""

    async def admit(self, tenant_id: uuid.UUID, attribution: Attribution | None) -> None:
        """Raises BudgetExceededError (an LLMError) when a blocking budget is used up."""
        ...

    async def record(
        self, tenant_id: uuid.UUID, attribution: Attribution | None, call: ModelCall
    ) -> None:
        """Records one usage event. Never raises: a failure is logged and counted."""
        ...
