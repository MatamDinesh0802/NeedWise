"""A scripted language model for tests: no network, records every request."""

import asyncio
from collections.abc import AsyncIterator, Sequence
from typing import Any

from workmind.providers.llm import Done, LLMRequest, LLMUsage, Role, StreamEvent, TextDelta


class FakeLLM:
    def __init__(
        self,
        reply: str | Sequence[str] = "Parental leave is 16 weeks [S1].",
        *,
        rewrite: str | None = None,
        external: bool = True,
        delay: float = 0.0,
    ) -> None:
        self.tokens = [reply] if isinstance(reply, str) else list(reply)
        self.rewrite = rewrite
        self.external = external
        self.delay = delay
        self.requests: list[tuple[Role, LLMRequest]] = []

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
        raise NotImplementedError

    def roles(self) -> list[Role]:
        return [role for role, _ in self.requests]
