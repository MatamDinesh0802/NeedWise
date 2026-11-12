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
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Literal, Protocol

import anthropic
import httpx
from anthropic.types.beta import BetaMessage
from pydantic import BaseModel

Role = Literal["answer", "router", "judge", "generator", "agent"]
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
    # None: the role's configured model. Set by PinnedModel (an agent configured with its own
    # model, docs/agents.md): the same provider, a different model for this one request.
    model: str | None = None


@dataclass
class LLMUsage:
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    stop_reason: str = ""
    # Set by the tenant resolver (modules/model_catalog/resolver.py): the catalog model that
    # served the call, and the tenant's price for it (USD per million input, output tokens).
    catalog_model: str = ""
    price_per_mtok: tuple[float, float] | None = None


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class Done:
    usage: LLMUsage = field(default_factory=LLMUsage)


StreamEvent = TextDelta | Done


@dataclass(frozen=True)
class ToolSpec:
    """A tool offered to the model: name, purpose, JSON schema of its input."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


# Runs one call and returns (content for the model, is_error). Gating, auditing and sanitizing
# happen in the caller's executor (modules/tools/execute.py), never in the adapters.
ToolExecutor = Callable[[ToolCall], Awaitable[tuple[str, bool]]]


@dataclass
class ToolLoopResult:
    text: str
    calls: list[ToolCall] = field(default_factory=list[ToolCall])
    usage: list[LLMUsage] = field(default_factory=list[LLMUsage])
    stopped: Literal["done", "max_steps"] = "done"


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

    async def run_tools(
        self,
        role: Role,
        request: LLMRequest,
        tools: Sequence[ToolSpec],
        execute: ToolExecutor,
        *,
        max_steps: int = 6,
    ) -> ToolLoopResult: ...


class AnthropicLLM:
    external = True  # requests leave your infrastructure (ARCHITECTURE 11.6)

    def __init__(
        self,
        roles: Mapping[Role, RoleConfig],
        *,
        api_key: str | None,
        fallbacks: bool = True,
        client: anthropic.AsyncAnthropic | None = None,
    ) -> None:
        # api_key=None lets the SDK resolve ANTHROPIC_API_KEY / `ant auth login` profiles itself.
        # A tenant's provider passes its own client (tenant_client): its key, its base URL.
        self._client = client or anthropic.AsyncAnthropic(
            api_key=api_key, max_retries=3, timeout=300
        )
        self._roles = roles
        self._fallbacks = fallbacks

    def _params(self, role: Role, request: LLMRequest) -> dict[str, Any]:
        config = self._roles[role]
        model = request.model or config.model
        params: dict[str, Any] = {
            "model": model,
            "max_tokens": request.max_tokens,
            "system": request.system,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "output_config": {"effort": request.effort or config.effort},
        }
        if self._fallbacks and model in FALLBACK_MODELS:
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

    async def run_tools(
        self,
        role: Role,
        request: LLMRequest,
        tools: Sequence[ToolSpec],
        execute: ToolExecutor,
        *,
        max_steps: int = 6,
    ) -> ToolLoopResult:
        """A manual tool loop behind the provider interface (local models need the same one).
        The full assistant content (thinking and tool_use blocks) goes back each turn."""
        params = self._params(role, request)
        params.pop("betas", None)  # no model fallback mid-loop: thinking is tied to one model
        params.pop("fallbacks", None)
        params["tools"] = [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema}
            for t in tools
        ]
        messages: list[dict[str, Any]] = list(params["messages"])
        result = ToolLoopResult(text="")
        for _ in range(max_steps):
            try:
                params["messages"] = messages
                message: BetaMessage = await self._client.beta.messages.create(**params)
            except anthropic.APIError as exc:
                raise LLMError(f"Claude API error: {exc}") from exc
            result.usage.append(self._usage(message))
            text = "".join(b.text for b in message.content if b.type == "text")
            if message.stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": message.content})
                continue
            uses = [b for b in message.content if b.type == "tool_use"]
            if message.stop_reason != "tool_use" or not uses:
                result.text = text
                return result
            messages.append({"role": "assistant", "content": message.content})
            replies: list[dict[str, Any]] = []
            for use in uses:
                call = ToolCall(
                    use.id, use.name, dict(use.input) if isinstance(use.input, dict) else {}
                )
                result.calls.append(call)
                content, is_error = await execute(call)
                replies.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": use.id,
                        "content": content,
                        "is_error": is_error,
                    }
                )
            messages.append({"role": "user", "content": replies})
            result.text = text
        result.stopped = "max_steps"
        return result


def tenant_client(
    *, api_key: str, base_url: str | None, timeout: float = 300, max_retries: int = 3
) -> anthropic.AsyncAnthropic:
    """A client for a tenant's own Anthropic key. Explicit credentials and URL, so the SDK never
    falls back to the deployment's environment (ANTHROPIC_API_KEY, profiles, ANTHROPIC_BASE_URL);
    no redirects, so the key goes to `base_url` and nowhere else."""
    return anthropic.AsyncAnthropic(
        api_key=api_key,
        base_url=base_url or "https://api.anthropic.com",
        max_retries=max_retries,
        timeout=timeout,
        http_client=anthropic.DefaultAsyncHttpxClient(follow_redirects=False),
    )


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
        timeout: float = 300,
    ) -> None:
        self.external = external
        self._http = http
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._roles = roles
        self._timeout = timeout

    def _body(self, role: Role, request: LLMRequest, **extra: Any) -> dict[str, Any]:
        return {
            "model": request.model or self._roles[role].model,
            "max_tokens": request.max_tokens,
            "messages": [{"role": "system", "content": request.system}]
            + [{"role": m.role, "content": m.content} for m in request.messages],
            **extra,
        }

    async def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._http.post(
                self._url,
                json=body,
                headers=self._headers,
                timeout=self._timeout,
                follow_redirects=False,  # the key goes to this URL only
            )
        except httpx.HTTPError as exc:
            raise LLMError(f"model server unreachable at {self._url}: {exc!r}") from exc
        if response.is_error:
            raise LLMError(f"model server HTTP {response.status_code}: {response.text[:200]}")
        return response.json()

    async def run_tools(
        self,
        role: Role,
        request: LLMRequest,
        tools: Sequence[ToolSpec],
        execute: ToolExecutor,
        *,
        max_steps: int = 6,
    ) -> ToolLoopResult:
        body = self._body(role, request)
        body["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            }
            for t in tools
        ]
        messages: list[dict[str, Any]] = list(body["messages"])
        result = ToolLoopResult(text="")
        for _ in range(max_steps):
            reply = await self._post({**body, "messages": messages})
            result.usage.append(self._usage(reply))
            message = reply["choices"][0]["message"]
            calls = message.get("tool_calls") or []
            result.text = message.get("content") or ""
            if not calls:
                return result
            messages.append(message)
            for raw in calls:
                try:
                    arguments = json.loads(raw["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                call = ToolCall(
                    raw["id"],
                    raw["function"]["name"],
                    arguments if isinstance(arguments, dict) else {},
                )
                result.calls.append(call)
                content, is_error = await execute(call)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": raw["id"],
                        "content": ("ERROR: " if is_error else "") + content,
                    }
                )
        result.stopped = "max_steps"
        return result

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
        usage = LLMUsage(model=request.model or self._roles[role].model)
        body = self._body(role, request, stream=True, stream_options={"include_usage": True})
        try:
            async with self._http.stream(
                "POST",
                self._url,
                json=body,
                headers=self._headers,
                timeout=self._timeout,
                follow_redirects=False,
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


class PinnedModel:
    """Sends every request to one model of the wrapped provider, whatever the role says.

    An agent configured with its own model (docs/agents.md) runs through this. Everything else
    (effort per role, the provider, whether requests leave your infrastructure) stays the
    wrapped provider's, so sensitivity routing still sees the right `external` flag."""

    def __init__(self, inner: LLMProvider, model: str) -> None:
        self.inner = inner
        self.model = model
        self.external = inner.external

    def _pin(self, request: LLMRequest) -> LLMRequest:
        return replace(request, model=self.model)

    async def complete(self, role: Role, request: LLMRequest) -> tuple[str, LLMUsage]:
        return await self.inner.complete(role, self._pin(request))

    async def stream(self, role: Role, request: LLMRequest) -> AsyncIterator[StreamEvent]:
        async for event in self.inner.stream(role, self._pin(request)):
            yield event

    async def structured[T: BaseModel](
        self, role: Role, request: LLMRequest, schema: type[T]
    ) -> tuple[T, LLMUsage]:
        return await self.inner.structured(role, self._pin(request), schema)

    async def run_tools(
        self,
        role: Role,
        request: LLMRequest,
        tools: Sequence[ToolSpec],
        execute: ToolExecutor,
        *,
        max_steps: int = 6,
    ) -> ToolLoopResult:
        return await self.inner.run_tools(
            role, self._pin(request), tools, execute, max_steps=max_steps
        )


def _strict_schema(schema: type[BaseModel]) -> dict[str, Any]:
    """JSON schema with additionalProperties: false and every property required, as strict
    structured outputs expect. Optional fields become nullable instead of omittable."""
    raw = schema.model_json_schema()

    def tighten(node: Any, *, names: bool = False) -> Any:  # pyright: ignore[reportUnknownParameterType]
        if isinstance(node, dict) and names:
            # The keys of "properties" and "$defs" are names: a field may be called "title".
            return {k: tighten(v) for k, v in node.items()}  # pyright: ignore[reportUnknownVariableType]
        if isinstance(node, dict):
            skip = ("title", "default")  # annotations, not constraints
            node = {
                k: tighten(v, names=k in ("properties", "$defs"))
                for k, v in node.items()  # pyright: ignore[reportUnknownVariableType]
                if k not in skip
            }
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"])
            return node  # pyright: ignore[reportUnknownVariableType]
        if isinstance(node, list):
            return [tighten(v) for v in node]  # pyright: ignore[reportUnknownVariableType]
        return node

    return tighten(raw)
