"""Usage accounting without a database: periods, costs, budget words, attribution, and the
metering seam in the tenant resolver (one recorded call per model call, whatever happens)."""

import json
import uuid
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from pydantic import BaseModel, ValidationError

from needwise.modules.model_catalog.resolver import Target, TenantLLM
from needwise.modules.usage.budgets import (
    Binding,
    BudgetExceededError,
    BudgetRule,
    period_window,
    state_of,
    status_of,
)
from needwise.modules.usage.calls import Attribution, ModelCall, attribute, current, refine
from needwise.modules.usage.meter import call_cost, warning_text
from needwise.providers.llm import (
    Done,
    LLMError,
    LLMMessage,
    LLMRefusalError,
    LLMRequest,
    LLMUsage,
    OpenAICompatibleLLM,
    Role,
    RoleConfig,
    StreamEvent,
    TextDelta,
    ToolCall,
    ToolExecutor,
    ToolLoopResult,
    ToolSpec,
    UsageProbe,
)
from tests.fakes import FakeLLM
from tests.test_llm_adapters import Block, Message, anthropic_with

REQUEST = LLMRequest(system="sys", messages=[LLMMessage("user", "question")])
ROLES: tuple[Role, ...] = ("answer", "router", "judge", "generator", "agent")
TENANT = uuid.UUID(int=1)
ALICE = uuid.UUID(int=2)


# --- Periods and states ----------------------------------------------------------------------


def test_periods_are_utc_days_and_calendar_months() -> None:
    late = datetime(2026, 12, 31, 23, 59, tzinfo=UTC)
    assert period_window("day", late) == (
        datetime(2026, 12, 31, tzinfo=UTC),
        datetime(2027, 1, 1, tzinfo=UTC),
    )
    assert period_window("month", late) == (
        datetime(2026, 12, 1, tzinfo=UTC),
        datetime(2027, 1, 1, tzinfo=UTC),
    )
    # A time in another zone counts in the UTC period it falls in.
    assert period_window("month", datetime.fromisoformat("2026-11-30T20:00:00-05:00"))[0] == (
        datetime(2026, 12, 1, tzinfo=UTC)
    )


def test_a_budget_warns_at_its_level_and_is_exceeded_at_its_limit() -> None:
    assert state_of(79.99, 100, 0.8) == "ok"
    assert state_of(80, 100, 0.8) == "warning"
    assert state_of(100, 100, 0.8) == "exceeded"


def _rule(scope: str = "tenant", period: str = "month", action: str = "block") -> BudgetRule:
    return BudgetRule(uuid.UUID(int=9), scope, None, period, 500.0, 0.8, action)


def test_the_exceeded_error_carries_the_status_for_the_api_and_the_stream() -> None:
    now = datetime(2026, 11, 12, 10, tzinfo=UTC)
    status = status_of(Binding(_rule(), None), 512.4, None, now)
    error = BudgetExceededError(status)

    problem = error.problem()
    event = error.event()

    assert isinstance(error, LLMError)  # every handler of a model that cannot answer fails closed
    assert (problem.status, problem.code) == (429, "budget_exceeded")
    assert problem.extra["budget"] == {
        "id": str(uuid.UUID(int=9)),
        "scope": "tenant",
        "subject": None,
        "period": "month",
        "limit_usd": 500.0,
        "spent_usd": 512.4,
        "warn_at": 0.8,
        "status": "exceeded",
        "resets_at": "2026-12-01T00:00:00Z",
    }
    assert error.detail == (
        "The monthly organization budget of $500.00 is used up ($512.40 spent). Requests that "
        "use an AI model are paused until it resets at 2026-12-01 00:00 UTC."
    )
    assert event == {
        "code": "budget_exceeded",
        "message": error.detail,
        "detail": error.detail,
        "budget": problem.extra["budget"],
    }


def test_warnings_say_how_much_is_spent_and_what_happens_next() -> None:
    now = datetime(2026, 11, 12, tzinfo=UTC)
    cap = BudgetRule(uuid.UUID(int=8), "user", ALICE, "day", 5.0, 0.8, "block")
    title, body = warning_text(status_of(Binding(cap, ALICE), 4.1, None, now))
    assert title == "Your daily cap is 82% used"
    assert body == (
        "$4.10 of $5.00 spent (82%). Once it is used up, requests that use an AI model are "
        "refused until it resets. It resets at 2026-11-13 00:00 UTC."
    )
    only_warns = status_of(Binding(_rule(action="warn"), None), 600, None, now)
    title, body = warning_text(only_warns)
    assert title == "The monthly organization budget is used up"
    assert "only warns: requests keep working" in body
    assert "2026-12-01 00:00 UTC" in body


# --- Costs ------------------------------------------------------------------------------------


def _call(usages: Sequence[LLMUsage], model: str = "m", price: Any = None) -> ModelCall:
    return ModelCall(
        method="complete",
        role="answer",
        model_id=f"p:{model}",
        model=model,
        provider="openai_compatible",
        local=False,
        status="ok",
        latency_ms=5,
        usages=list(usages),
        price_per_mtok=price,
    )


def test_cost_uses_the_catalog_price_then_the_static_table_and_never_guesses() -> None:
    catalog = LLMUsage(
        model="x", input_tokens=1_000_000, output_tokens=0, price_per_mtok=(3.0, 15.0)
    )
    static = LLMUsage(model="claude-sonnet-5-5", input_tokens=0, output_tokens=100_000)
    unknown = LLMUsage(model="llama-local", input_tokens=10, output_tokens=1)

    assert call_cost(_call([catalog])) == pytest.approx(3.0)
    assert call_cost(_call([catalog, static])) == pytest.approx(4.0)
    assert call_cost(_call([catalog, unknown])) is None  # one unpriced response: unknown
    # A call that used nothing costs nothing when the model has a price, else it is unknown.
    assert call_cost(_call([], model="claude-opus-5-5")) == 0.0
    assert call_cost(_call([], model="llama-local")) is None
    assert call_cost(_call([], model="llama-local", price=(1.0, 1.0))) == 0.0


# --- Attribution ------------------------------------------------------------------------------


def test_attribution_is_scoped_to_its_block_and_can_be_narrowed() -> None:
    assert current() is None
    with attribute("chat", user_id=ALICE, ref=("message", 7)) as outer:
        assert current() == outer
        assert (outer.ref_type, outer.ref_id) == ("message", "7")
        with refine(agent_key="hr"):
            narrowed = current()
            assert narrowed is not None
            assert (narrowed.feature, narrowed.agent_key, narrowed.user_id) == ("chat", "hr", ALICE)
        assert current() == outer
    assert current() is None
    with refine(agent_key="hr"):  # nothing to narrow
        assert current() is None


def test_an_evaluation_run_is_attributed_to_eval_and_never_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from needwise.evals import __main__ as evals

    seen: list[Attribution | None] = []

    async def run(args: Any) -> int:
        seen.append(current())
        return 0

    monkeypatch.setattr(evals, "run", run)
    assert evals.main([]) == 0
    assert seen == [Attribution(feature="eval", user_id=None, enforce=False)]


# --- The metering seam ------------------------------------------------------------------------


class RecordingMeter:
    def __init__(self, refuse: BudgetExceededError | None = None, refuse_at: float = 0.0) -> None:
        self.refuse = refuse
        self.refuse_at = refuse_at  # refuse once the call in progress has used this much
        self.admitted: list[Attribution | None] = []
        self.pending: list[float] = []
        self.calls: list[tuple[Attribution | None, ModelCall]] = []

    async def admit(
        self, tenant_id: uuid.UUID, attribution: Attribution | None, *, pending_usd: float = 0.0
    ) -> None:
        self.admitted.append(attribution)
        self.pending.append(pending_usd)
        if self.refuse is not None and pending_usd >= self.refuse_at:
            raise self.refuse

    async def record(
        self, tenant_id: uuid.UUID, attribution: Attribution | None, call: ModelCall
    ) -> None:
        assert tenant_id == TENANT
        self.calls.append((attribution, call))


def metered(client: Any, meter: RecordingMeter, *, price: Any = None) -> TenantLLM:
    target = Target(
        "system:claude-test", client, True, price=price, name="claude-test", provider="anthropic"
    )
    return TenantLLM(
        dict.fromkeys(ROLES, target),
        max_external_sensitivity="internal",
        local=None,
        tenant_id=TENANT,
        meter=meter,
    )


class _Out(BaseModel):
    answer: str


async def test_every_kind_of_call_records_exactly_one_event_with_its_usage() -> None:
    meter = RecordingMeter()
    fake = FakeLLM(["a", "b"], structured=[_Out(answer="x")], tool_turns=[[]])
    llm = metered(fake, meter)

    async def execute(call: Any) -> tuple[str, bool]:
        return "", False

    with attribute("chat", user_id=ALICE):
        await llm.complete("router", REQUEST)
        _ = [e async for e in llm.stream("answer", REQUEST)]
        await llm.structured("router", REQUEST, _Out)
        await llm.run_tools("agent", REQUEST, [], execute)

    methods = [(call.method, call.status) for _, call in meter.calls]
    assert methods == [
        ("complete", "ok"),
        ("stream", "ok"),
        ("structured", "ok"),
        ("run_tools", "ok"),
    ]
    assert [(c.input_tokens, c.output_tokens) for _, c in meter.calls] == [
        (20, 5),
        (100, 2),
        (30, 8),
        (50 + 60, 10 + 12),  # one event for the whole tool loop, every turn counted
    ]
    first = meter.calls[0][1]
    assert (first.model_id, first.model, first.provider, first.local) == (
        "system:claude-test",
        "fake",
        "anthropic",
        False,
    )
    assert all(a == Attribution(feature="chat", user_id=ALICE) for a, _ in meter.calls)
    assert all(u.catalog_model == "system:claude-test" for _, c in meter.calls for u in c.usages)


class ProbeClient:
    """Reports usage to the request's probe the way the real adapters do, then misbehaves."""

    external = True

    async def complete(self, role: Role, request: LLMRequest) -> tuple[str, LLMUsage]:
        raise LLMError("server error before any usage")

    async def stream(self, role: Role, request: LLMRequest) -> AsyncIterator[StreamEvent]:
        assert request.probe is not None
        request.probe.partial = LLMUsage(model="claude-test", input_tokens=900, output_tokens=1)
        yield TextDelta("Hel")
        request.probe.partial = LLMUsage(model="claude-test", input_tokens=900, output_tokens=40)
        yield TextDelta("lo")
        yield Done(LLMUsage(model="claude-test", input_tokens=900, output_tokens=41))

    async def structured(self, role: Role, request: LLMRequest, schema: Any) -> Any:
        raise LLMError("unreachable")

    async def run_tools(
        self,
        role: Role,
        request: LLMRequest,
        tools: Sequence[ToolSpec],
        execute: ToolExecutor,
        *,
        max_steps: int = 6,
    ) -> ToolLoopResult:
        assert request.probe is not None
        request.probe.add(LLMUsage(model="claude-test", input_tokens=300, output_tokens=20))
        request.probe.add(LLMUsage(model="claude-test", input_tokens=400, output_tokens=30))
        raise LLMError("the third turn failed")


async def test_a_stream_stopped_early_is_recorded_with_what_was_used_so_far() -> None:
    meter = RecordingMeter()
    llm = metered(ProbeClient(), meter)

    stream = llm.stream("answer", REQUEST)
    async for event in stream:
        assert isinstance(event, TextDelta)
        break  # the person pressed stop
    await stream.aclose()  # type: ignore[attr-defined]

    ((_, call),) = meter.calls
    assert (call.status, call.input_tokens, call.output_tokens) == ("cancelled", 900, 1)


async def test_a_stream_read_to_the_end_is_complete_even_if_closed_after_done() -> None:
    meter = RecordingMeter()
    llm = metered(ProbeClient(), meter)

    stream = llm.stream("answer", REQUEST)
    async for event in stream:
        if isinstance(event, Done):
            break
    await stream.aclose()  # type: ignore[attr-defined]

    ((_, call),) = meter.calls
    assert (call.status, call.output_tokens) == ("ok", 41)


async def test_failures_are_recorded_with_partial_usage_and_still_raise() -> None:
    meter = RecordingMeter()
    llm = metered(ProbeClient(), meter, price=(1.0, 1.0))

    with pytest.raises(LLMError):
        await llm.complete("router", REQUEST)
    with pytest.raises(LLMError, match="third turn"):
        await llm.run_tools("agent", REQUEST, [], None)  # type: ignore[arg-type]

    (_, failed), (_, partial) = meter.calls
    assert (failed.status, failed.usages) == ("failed", [])
    assert call_cost(failed) == 0.0  # nothing reported, a priced model: nothing to pay
    assert (partial.status, partial.input_tokens, partial.output_tokens) == ("failed", 700, 50)
    assert call_cost(partial) == pytest.approx(750 / 1_000_000)


async def test_a_refused_call_never_reaches_the_model_and_is_not_recorded() -> None:
    status = status_of(Binding(_rule(), None), 600, None, datetime(2026, 11, 12, tzinfo=UTC))
    meter = RecordingMeter(refuse=BudgetExceededError(status))
    fake = FakeLLM()
    llm = metered(fake, meter)

    with attribute("analytics", user_id=ALICE), pytest.raises(BudgetExceededError):
        await llm.complete("router", REQUEST)
    with pytest.raises(BudgetExceededError):
        _ = [e async for e in llm.stream("answer", REQUEST)]

    assert fake.requests == []
    assert meter.calls == []
    assert meter.admitted[0] == Attribution(feature="analytics", user_id=ALICE)


class LoopClient:
    """A tool loop like the real adapters run it: each model turn reports its usage to the
    probe, then the turn's tools run, then the next turn."""

    external = True

    def __init__(self, turns: int) -> None:
        self.turns = turns
        self.model_turns = 0

    async def run_tools(
        self,
        role: Role,
        request: LLMRequest,
        tools: Sequence[ToolSpec],
        execute: ToolExecutor,
        *,
        max_steps: int = 6,
    ) -> ToolLoopResult:
        assert request.probe is not None
        result = ToolLoopResult(text="")
        for number in range(self.turns):
            self.model_turns += 1
            usage = LLMUsage(model="claude-test", input_tokens=40_000, output_tokens=8_000)
            request.probe.add(usage)
            result.usage.append(usage)
            if number < self.turns - 1:
                await execute(ToolCall(f"c{number}", "search_knowledge", {}))
        result.text = "done"
        return result


async def test_a_tool_loop_is_checked_before_every_further_turn_counting_its_own_usage() -> None:
    """docs/usage.md: the budget test runs before every model call, including each turn of a
    tool loop (research, Insights, department agents), with the loop's own usage counted."""
    status = status_of(Binding(_rule(), None), 600, None, datetime(2026, 11, 12, tzinfo=UTC))
    # $1 in / $5 out per MTok: a turn of 40,000 in and 8,000 out costs $0.08.
    meter = RecordingMeter(refuse=BudgetExceededError(status), refuse_at=0.15)
    client = LoopClient(turns=6)
    llm = metered(client, meter, price=(1.0, 5.0))
    ran: list[str] = []

    async def execute(call: Any) -> tuple[str, bool]:
        ran.append(call.id)
        return "", False

    with attribute("research", user_id=ALICE), pytest.raises(BudgetExceededError):
        await llm.run_tools("agent", REQUEST, [], execute)

    assert meter.pending == [0.0, pytest.approx(0.08), pytest.approx(0.16)]
    assert client.model_turns == 2  # the third turn was never made
    assert ran == ["c0"]  # the second turn's tools did not run either
    ((_, call),) = meter.calls  # still one event for the loop, with both turns
    assert (call.status, call.input_tokens, call.output_tokens) == ("failed", 80_000, 16_000)

    within = RecordingMeter()
    whole = LoopClient(turns=3)
    await metered(whole, within, price=(1.0, 5.0)).run_tools("agent", REQUEST, [], execute)
    assert within.pending == [0.0, pytest.approx(0.08), pytest.approx(0.16)]
    assert whole.model_turns == 3


async def test_a_structured_reply_that_fails_validation_is_still_counted() -> None:
    reply = {
        "model": "local-model",
        "choices": [{"message": {"content": '{"wrong": true}'}}],
        "usage": {"prompt_tokens": 70, "completion_tokens": 9},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        json.loads(request.content)
        return httpx.Response(200, json=reply)

    roles: dict[Role, RoleConfig] = {
        r: RoleConfig(model="local-model", effort="low") for r in ROLES
    }
    meter = RecordingMeter()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        llm = metered(OpenAICompatibleLLM(http, roles, base_url="http://local/v1"), meter)
        with pytest.raises(ValidationError):
            await llm.structured("router", REQUEST, _Out)

    ((_, call),) = meter.calls
    assert (call.status, call.input_tokens, call.output_tokens, call.model) == (
        "failed",
        70,
        9,
        "local-model",
    )


async def test_a_refusal_is_billed_and_reported_before_it_raises() -> None:
    llm, _ = anthropic_with([Message([Block(type="text", text="")], "refusal")])
    probe = UsageProbe()
    request = LLMRequest(system="s", messages=[LLMMessage("user", "q")], probe=probe)

    with pytest.raises(LLMRefusalError):
        await llm.complete("answer", request)

    assert [(u.input_tokens, u.output_tokens) for u in probe.seen] == [(10, 5)]


class ScriptedStream:
    """The SDK's message stream: events, a snapshot of the message so far, the final message."""

    def __init__(self, steps: list[tuple[Any, Message]], final: Message) -> None:
        self.steps = steps
        self.final = final
        self.current_message_snapshot = steps[0][1]

    async def __aenter__(self) -> "ScriptedStream":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def _events(self) -> AsyncIterator[Any]:
        for event, snapshot in self.steps:
            self.current_message_snapshot = snapshot
            yield event

    def __aiter__(self) -> AsyncIterator[Any]:
        return self._events()

    async def get_final_message(self) -> Message:
        return self.final


async def test_a_stopped_claude_stream_reports_what_it_had_used() -> None:
    from types import SimpleNamespace

    from tests.test_llm_adapters import Usage

    def snapshot(output_tokens: int) -> Message:
        return Message([], "", usage=Usage(input_tokens=1200, output_tokens=output_tokens))

    text = SimpleNamespace(
        type="content_block_delta", delta=SimpleNamespace(type="text_delta", text="Hi")
    )
    steps = [
        (SimpleNamespace(type="message_start"), snapshot(1)),
        (text, snapshot(1)),
        (SimpleNamespace(type="message_delta"), snapshot(37)),
        (text, snapshot(37)),
    ]
    llm, _ = anthropic_with([])
    llm._client.beta.messages.stream = lambda **params: ScriptedStream(  # type: ignore[attr-defined]
        steps, snapshot(40)
    )
    probe = UsageProbe()
    request = LLMRequest(system="s", messages=[LLMMessage("user", "q")], probe=probe)

    stream = llm.stream("answer", request)
    seen = 0
    async for event in stream:
        seen += isinstance(event, TextDelta)
        if seen == 2:
            break  # stopped after the second piece of text
    await stream.aclose()  # type: ignore[attr-defined]

    assert probe.responses == []
    assert probe.partial is not None
    assert (probe.partial.input_tokens, probe.partial.output_tokens) == (1200, 37)
