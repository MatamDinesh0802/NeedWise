"""The provider adapters' request shapes, without any network: the Anthropic tool loop against a
scripted client, the OpenAI-compatible one against a mocked HTTP server."""

import json
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from workmind.providers.llm import (
    AnthropicLLM,
    LLMMessage,
    LLMNotConfiguredError,
    LLMRequest,
    OpenAICompatibleLLM,
    RoleConfig,
    ToolCall,
    ToolSpec,
    _strict_schema,  # pyright: ignore[reportPrivateUsage]
)

ROLES = {
    role: RoleConfig(model="claude-opus-5-5", effort="low")
    for role in ("answer", "router", "judge", "generator", "agent")
}
TOOLS = [
    ToolSpec(
        "lookup", "Look something up", {"type": "object", "properties": {"q": {"type": "string"}}}
    )
]
REQUEST = LLMRequest(system="sys", messages=[LLMMessage("user", "question")])


@dataclass
class Block:
    type: str
    text: str = ""
    id: str = ""
    name: str = ""
    input: dict[str, Any] = field(default_factory=dict[str, Any])


@dataclass
class Usage:
    input_tokens: int = 10
    output_tokens: int = 5
    cache_read_input_tokens: int = 0


@dataclass
class Message:
    content: list[Block]
    stop_reason: str
    model: str = "claude-opus-5-5"
    usage: Usage = field(default_factory=Usage)


class ScriptedMessages:
    def __init__(self, replies: list[Message]) -> None:
        self.replies = replies
        self.requests: list[dict[str, Any]] = []

    async def create(self, **params: Any) -> Message:
        self.requests.append({**params, "messages": list(params["messages"])})
        return self.replies.pop(0)


def anthropic_with(replies: list[Message]) -> tuple[AnthropicLLM, ScriptedMessages]:
    llm = AnthropicLLM(ROLES, api_key="test", fallbacks=True)  # type: ignore[arg-type]
    scripted = ScriptedMessages(replies)

    class Beta:
        messages = scripted

    class FakeClient:
        beta = Beta()

    llm._client = FakeClient()  # type: ignore[assignment]
    return llm, scripted


async def test_the_anthropic_tool_loop_echoes_full_content_and_matches_tool_ids() -> None:
    thinking = Block(type="thinking", text="")
    llm, scripted = anthropic_with(
        [
            Message(
                [thinking, Block(type="tool_use", id="tu_1", name="lookup", input={"q": "x"})],
                "tool_use",
            ),
            Message([Block(type="text", text="The answer.")], "end_turn"),
        ]
    )
    seen: list[ToolCall] = []

    async def execute(call: ToolCall) -> tuple[str, bool]:
        seen.append(call)
        return "result text", False

    result = await llm.run_tools("agent", REQUEST, TOOLS, execute)

    second = scripted.requests[1]
    assert result.text == "The answer."
    assert seen == [ToolCall("tu_1", "lookup", {"q": "x"})]
    assert second["messages"][1]["role"] == "assistant"
    assert second["messages"][1]["content"][0] is thinking  # thinking goes back untouched
    assert second["messages"][2]["content"] == [
        {"type": "tool_result", "tool_use_id": "tu_1", "content": "result text", "is_error": False}
    ]
    assert "fallbacks" not in second  # no model switch in the middle of a loop
    assert second["tools"][0]["input_schema"]["properties"]["q"] == {"type": "string"}
    assert len(result.usage) == 2


# What anthropic.AsyncAnthropic raises at request time when it finds no key, token or profile.
NO_AUTH = TypeError(
    '"Could not resolve authentication method. Expected one of api_key, auth_token, or '
    'credentials to be set. Or for the `Authorization` header to be explicitly omitted"'
)


def anthropic_raising(error: Exception) -> AnthropicLLM:
    llm = AnthropicLLM(ROLES, api_key="test", fallbacks=True)  # type: ignore[arg-type]

    class Messages:
        async def create(self, **params: Any) -> Message:
            raise error

        def stream(self, **params: Any) -> Any:
            raise error

    class Beta:
        messages = Messages()

    class FakeClient:
        beta = Beta()

    llm._client = FakeClient()  # type: ignore[assignment]
    return llm


async def test_a_missing_anthropic_key_is_a_model_that_is_not_configured() -> None:
    """A fresh install without ANTHROPIC_API_KEY: the chat says so instead of failing vaguely."""
    llm = anthropic_raising(NO_AUTH)

    with pytest.raises(LLMNotConfiguredError, match="ANTHROPIC_API_KEY"):
        await llm.complete("answer", REQUEST)
    with pytest.raises(LLMNotConfiguredError):
        async for _ in llm.stream("answer", REQUEST):
            pass

    async def execute(call: ToolCall) -> tuple[str, bool]:
        return "", False

    with pytest.raises(LLMNotConfiguredError):
        await llm.run_tools("agent", REQUEST, TOOLS, execute)


async def test_any_other_type_error_from_the_anthropic_client_is_not_hidden() -> None:
    llm = anthropic_raising(TypeError("unexpected keyword argument 'betas'"))

    with pytest.raises(TypeError, match="betas"):
        await llm.complete("answer", REQUEST)


async def test_the_anthropic_tool_loop_stops_at_its_step_limit() -> None:
    use = Message([Block(type="tool_use", id="t", name="lookup", input={})], "tool_use")
    llm, _ = anthropic_with([use, use, use])

    async def execute(call: ToolCall) -> tuple[str, bool]:
        return "again", False

    result = await llm.run_tools("agent", REQUEST, TOOLS, execute, max_steps=3)

    assert (result.stopped, len(result.calls)) == ("max_steps", 3)


async def test_the_openai_compatible_tool_loop_uses_tool_messages() -> None:
    bodies: list[dict[str, Any]] = []
    replies = [
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "c1",
                                "type": "function",
                                "function": {"name": "lookup", "arguments": '{"q": "y"}'},
                            }
                        ],
                    }
                }
            ],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3},
        },
        {"choices": [{"message": {"role": "assistant", "content": "Done."}}], "usage": {}},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=replies.pop(0))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        llm = OpenAICompatibleLLM(http, ROLES, base_url="http://local/v1")  # type: ignore[arg-type]

        async def execute(call: ToolCall) -> tuple[str, bool]:
            return "not allowed", True

        result = await llm.run_tools("agent", REQUEST, TOOLS, execute)

    assert result.text == "Done."
    assert bodies[0]["tools"][0]["function"]["name"] == "lookup"
    assert bodies[1]["messages"][-1] == {
        "role": "tool",
        "tool_call_id": "c1",
        "content": "ERROR: not allowed",
    }


class _Chart(BaseModel):
    title: str
    default: str | None = None


class _Answer(BaseModel):
    narrative: str
    chart: _Chart | None


def test_a_strict_schema_keeps_fields_named_title_or_default() -> None:
    # The schema annotations "title" and "default" are dropped; fields with those names are not.
    # Dropping the field once left a chart's title out of the schema, and every answer failed.
    schema = _strict_schema(_Answer)

    chart = schema["$defs"]["_Chart"]
    assert chart["required"] == ["title", "default"]
    assert set(chart["properties"]) == {"title", "default"}
    assert chart["additionalProperties"] is False
    assert "title" not in chart["properties"]["title"]
    assert "default" not in chart["properties"]["default"]
    assert schema["required"] == ["narrative", "chart"]
