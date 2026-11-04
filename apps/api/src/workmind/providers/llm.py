# The SDK's overloads resolve to Unknown when parameters are assembled in a dict; results are
# annotated with the SDK's own types (BetaMessage) instead.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""LLM providers (ARCHITECTURE sections 12, 23). Two implementations behind one interface:

- AnthropicLLM: Claude through the official Anthropic SDK (the default).
- OpenAICompatibleLLM: any `/v1/chat/completions` server: Ollama, vLLM, llama.cpp, LM Studio.

Callers ask for a *role* ("answer", "router", "judge", "generator"); settings map each role to
a model, so one deployment can mix providers' models without touching business code.
"""

import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import anthropic
import httpx
from anthropic.types.beta import BetaMessage
from pydantic import BaseModel

Role = Literal["answer", "router", "judge", "generator"]
Effort = Literal["low", "medium", "high", "xhigh", "max"]

# Models that accept Anthropic's server-side refusal fallback (`fallbacks: "default"`).
FALLBACK_MODELS = frozenset(
    {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-1"}
)
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMError(RuntimeError):
    pass


class LLMRefusalError(LLMError):
    """The model (and any fallback) declined the request."""


@dataclass(frozen=True)
class LLMMessage:
    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class LLMRequest:
    system: str
    messages: list[LLMMessage]
    max_tokens: int = 16000
    effort: Effort | None = None  # None: the role's configured effort


@dataclass
class LLMUsage:
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    stop_reason: str = ""


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class Done:
    usage: LLMUsage = field(default_factory=LLMUsage)


StreamEvent = TextDelta | Done


@dataclass(frozen=True)
class RoleConfig:
    model: str
    effort: Effort


class LLMProvider(Protocol):
    external: bool  # True when requests leave your infrastructure (a hosted API)

    async def complete(self, role: Role, request: LLMRequest) -> tuple[str, LLMUsage]: ...

    def stream(self, role: Role, request: LLMRequest) -> AsyncIterator[StreamEvent]: ...

    async def structured[T: BaseModel](
        self, role: Role, request: LLMRequest, schema: type[T]
    ) -> tuple[T, LLMUsage]: ...


class AnthropicLLM:
    external = True  # requests leave your infrastructure (ARCHITECTURE 11.6)

    def __init__(
        self, roles: Mapping[Role, RoleConfig], *, api_key: str | None, fallbacks: bool = True
    ) -> None:
        # api_key=None lets the SDK resolve ANTHROPIC_API_KEY / `ant auth login` profiles itself.
        self._client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=3, timeout=300)
        self._roles = roles
        self._fallbacks = fallbacks

    def _params(self, role: Role, request: LLMRequest) -> dict[str, Any]:
        config = self._roles[role]
        params: dict[str, Any] = {
            "model": config.model,
            "max_tokens": request.max_tokens,
            "system": request.system,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "output_config": {"effort": request.effort or config.effort},
        }
        if self._fallbacks and config.model in FALLBACK_MODELS:
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = "default"  # re-run a declined request on Anthropic's pick
        return params

    @staticmethod
    def _usage(message: BetaMessage) -> LLMUsage:
        if message.stop_reason == "refusal":
            raise LLMRefusalError("the model declined this request")
        usage = message.usage
        return LLMUsage(
            model=message.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            stop_reason=message.stop_reason or "",
        )

    async def complete(self, role: Role, request: LLMRequest) -> tuple[str, LLMUsage]:
        try:
            message: BetaMessage = await self._client.beta.messages.create(
                **self._params(role, request)
            )
        except anthropic.APIError as exc:
            raise LLMError(f"Claude API error: {exc}") from exc
        usage = self._usage(message)
        return "".join(b.text for b in message.content if b.type == "text"), usage

    async def stream(self, role: Role, request: LLMRequest) -> AsyncIterator[StreamEvent]:
        try:
            async with self._client.beta.messages.stream(**self._params(role, request)) as stream:
                async for event in stream:
                    if event.type == "content_block_delta" and event.delta.type == "text_delta":
                        yield TextDelta(event.delta.text)
                final: BetaMessage = await stream.get_final_message()
        except anthropic.APIError as exc:
            raise LLMError(f"Claude API error: {exc}") from exc
        yield Done(self._usage(final))

    async def structured[T: BaseModel](
        self, role: Role, request: LLMRequest, schema: type[T]
    ) -> tuple[T, LLMUsage]:
        params = self._params(role, request)
        params["output_config"] = {
            **params["output_config"],
            "format": {"type": "json_schema", "schema": _strict_schema(schema)},
        }
        try:
            message: BetaMessage = await self._client.beta.messages.create(**params)
        except anthropic.APIError as exc:
            raise LLMError(f"Claude API error: {exc}") from exc
        usage = self._usage(message)
        text = next((b.text for b in message.content if b.type == "text"), "")
        return schema.model_validate_json(text), usage


class OpenAICompatibleLLM:
    """For local or self-hosted models. Effort has no equivalent here and is ignored."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        roles: Mapping[Role, RoleConfig],
        *,
        base_url: str,
        api_key: str | None = None,
        external: bool = False,
    ) -> None:
        self.external = external
        self._http = http
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._roles = roles

    def _body(self, role: Role, request: LLMRequest, **extra: Any) -> dict[str, Any]:
        return {
            "model": self._roles[role].model,
            "max_tokens": request.max_tokens,
            "messages": [{"role": "system", "content": request.system}]
            + [{"role": m.role, "content": m.content} for m in request.messages],
            **extra,
        }

    async def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._http.post(
                self._url, json=body, headers=self._headers, timeout=300
            )
        except httpx.HTTPError as exc:
            raise LLMError(f"model server unreachable at {self._url}: {exc!r}") from exc
        if response.is_error:
            raise LLMError(f"model server HTTP {response.status_code}: {response.text[:200]}")
        return response.json()

    @staticmethod
    def _usage(body: dict[str, Any]) -> LLMUsage:
        usage = body.get("usage") or {}
        return LLMUsage(
            model=body.get("model", ""),
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            stop_reason=(body.get("choices") or [{}])[0].get("finish_reason") or "",
        )

    async def complete(self, role: Role, request: LLMRequest) -> tuple[str, LLMUsage]:
        body = await self._post(self._body(role, request))
        return body["choices"][0]["message"]["content"] or "", self._usage(body)

    async def stream(self, role: Role, request: LLMRequest) -> AsyncIterator[StreamEvent]:
        usage = LLMUsage(model=self._roles[role].model)
        body = self._body(role, request, stream=True, stream_options={"include_usage": True})
        try:
            async with self._http.stream(
                "POST", self._url, json=body, headers=self._headers, timeout=300
            ) as response:
                if response.is_error:
                    raise LLMError(f"model server HTTP {response.status_code}")
                async for line in response.aiter_lines():
                    if not line.startswith("data: ") or line == "data: [DONE]":
                        continue
                    chunk = json.loads(line[6:])
                    if chunk.get("usage"):
                        usage = self._usage(chunk)
                    for choice in chunk.get("choices", []):
                        if text := (choice.get("delta") or {}).get("content"):
                            yield TextDelta(text)
        except httpx.HTTPError as exc:
            raise LLMError(f"model server unreachable at {self._url}: {exc!r}") from exc
        yield Done(usage)

    async def structured[T: BaseModel](
        self, role: Role, request: LLMRequest, schema: type[T]
    ) -> tuple[T, LLMUsage]:
        body = await self._post(
            self._body(
                role,
                request,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema.__name__,
                        "schema": _strict_schema(schema),
                        "strict": True,
                    },
                },
            )
        )
        return schema.model_validate_json(body["choices"][0]["message"]["content"]), self._usage(
            body
        )


def _strict_schema(schema: type[BaseModel]) -> dict[str, Any]:
    """JSON schema with additionalProperties: false and every property required, as strict
    structured outputs expect. Optional fields become nullable instead of omittable."""
    raw = schema.model_json_schema()

    def tighten(node: Any) -> Any:  # pyright: ignore[reportUnknownParameterType]
        if isinstance(node, dict):
            skip = ("title", "default")
            node = {k: tighten(v) for k, v in node.items() if k not in skip}  # pyright: ignore[reportUnknownVariableType]
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"])
            return node  # pyright: ignore[reportUnknownVariableType]
        if isinstance(node, list):
            return [tighten(v) for v in node]  # pyright: ignore[reportUnknownVariableType]
        return node

    return tighten(raw)
