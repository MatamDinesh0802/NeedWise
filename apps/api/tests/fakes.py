"""A scripted language model for tests: no network, records every request."""

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from workmind.providers.llm import (
    Done,
    LLMRequest,
    LLMUsage,
    Role,
    StreamEvent,
    TextDelta,
    ToolCall,
    ToolExecutor,
    ToolLoopResult,
    ToolSpec,
)

# One model turn of a tool loop: the tool calls the model makes in that turn, as (name, input).
ToolTurn = Sequence[tuple[str, Mapping[str, Any]]]


@dataclass(frozen=True)
class ToolReply:
    """What the executor returned to the model for one scripted call."""

    tool: str
    content: str
    is_error: bool


class FakeLLM:
    def __init__(
        self,
        reply: str | Sequence[str] = "Parental leave is 16 weeks [S1].",
        *,
        rewrite: str | None = None,
        external: bool = True,
        delay: float = 0.0,
        structured: Sequence[Any] = (),
        tool_turns: Sequence[ToolTurn] | None = None,
        tool_text: str = "Done.",
    ) -> None:
        self.tokens = [reply] if isinstance(reply, str) else list(reply)
        self.rewrite = rewrite
        self.external = external
        self.delay = delay
        self.requests: list[tuple[Role, LLMRequest]] = []
        self.structured_replies = list(structured)
        # run_tools replays these turns through the caller's executor (None: not scripted).
        self.tool_turns = None if tool_turns is None else [list(t) for t in tool_turns]
        self.tool_text = tool_text
        self.offered: list[ToolSpec] = []
        self.tool_replies: list[ToolReply] = []

    async def complete(self, role: Role, request: LLMRequest) -> tuple[str, LLMUsage]:
        self.requests.append((role, request))
        text = self.rewrite if role == "router" and self.rewrite else "A short summary."
        return text, LLMUsage(model="fake", input_tokens=20, output_tokens=5)

    async def stream(self, role: Role, request: LLMRequest) -> AsyncIterator[StreamEvent]:
        self.requests.append((role, request))
        for token in self.tokens:
            if self.delay:
                await asyncio.sleep(self.delay)
            yield TextDelta(token)
        yield Done(LLMUsage(model="fake", input_tokens=100, output_tokens=len(self.tokens)))

    async def structured(self, role: Role, request: LLMRequest, schema: Any) -> Any:
        self.requests.append((role, request))
        reply = self.structured_replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply, LLMUsage(model="fake", input_tokens=30, output_tokens=8)

    async def run_tools(
        self,
        role: Role,
        request: LLMRequest,
        tools: Sequence[ToolSpec],
        execute: ToolExecutor,
        *,
        max_steps: int = 6,
    ) -> ToolLoopResult:
        """Replays the scripted turns: each call goes through `execute` (the real executor),
        and its reply is kept in `tool_replies`. One usage entry per model turn."""
        if self.tool_turns is None:
            raise NotImplementedError("script the tool loop with tool_turns=[...]")
        self.requests.append((role, request))
        self.offered = list(tools)
        result = ToolLoopResult(text="")
        for number, turn in enumerate(self.tool_turns):
            if number >= max_steps:
                result.stopped = "max_steps"
                return result
            result.usage.append(LLMUsage(model="fake", input_tokens=50, output_tokens=10))
            for index, (name, arguments) in enumerate(turn):
                call = ToolCall(f"call-{number}-{index}", name, dict(arguments))
                result.calls.append(call)
                content, is_error = await execute(call)
                self.tool_replies.append(ToolReply(name, content, is_error))
        result.usage.append(LLMUsage(model="fake", input_tokens=60, output_tokens=12))
        result.text = self.tool_text
        return result

    def roles(self) -> list[Role]:
        return [role for role, _ in self.requests]
