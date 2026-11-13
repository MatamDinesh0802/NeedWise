"""The stub model server's own behaviour. apps/api/tests/test_stub_llm.py proves that
NeedWise's openai_compatible adapter works against it; these cover the stub's edges."""

import json
from typing import Any

import pytest
from starlette.testclient import TestClient

from stub_llm import Timing, create_app, instance

INSTANT = Timing(latency_ms=0, ttft_ms=0, tokens_per_s=0)
SOURCES = (
    '<sources>\n<source id="S1" title="Leave Policy">Parental leave is 16 weeks. More.</source>'
    "\n</sources>\n\nQuestion: How long is parental leave?"
)


def chat(client: TestClient, **body: Any) -> Any:
    response = client.post("/v1/chat/completions", json={"model": "m", **body})
    assert response.status_code == 200, response.text
    return response.json()


def test_a_stream_ends_with_usage_and_done() -> None:
    client = TestClient(
        create_app(Timing(latency_ms=0, ttft_ms=0, tokens_per_s=0, answer_tokens=30))
    )
    body = {
        "model": "m",
        "stream": True,
        "stream_options": {"include_usage": True},
        "messages": [{"role": "system", "content": "s"}, {"role": "user", "content": SOURCES}],
    }

    lines = [x for x in client.post("/v1/chat/completions", json=body).text.splitlines() if x]

    chunks = [json.loads(line[6:]) for line in lines[:-1]]
    text = "".join(c["choices"][0]["delta"].get("content") or "" for c in chunks if c["choices"])
    assert lines[-1] == "data: [DONE]"
    assert chunks[-1]["usage"]["completion_tokens"] == len(text.split()) == 30
    assert "Parental leave is 16 weeks [S1]." in text


def test_a_rewrite_returns_the_latest_message_and_anything_else_ok() -> None:
    client = TestClient(create_app(INSTANT))

    rewrite = chat(client, messages=[{"role": "user", "content": "x\n\nLatest message: and Q4?"}])
    probe = chat(client, messages=[{"role": "user", "content": "Reply with the word OK."}])

    assert rewrite["choices"][0]["message"]["content"] == "and Q4?"
    assert probe["choices"][0]["message"]["content"] == "OK"


def test_tools_are_called_once_then_answered() -> None:
    client = TestClient(create_app(INSTANT))
    tools = [{"type": "function", "function": {"name": "run_sql", "parameters": {}}}]
    system = "Rules.\n\nTables (use these names in run_sql):\n- invoice_lines (10 rows): Lines."
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": "Revenue?"},
    ]

    first = chat(client, messages=messages, tools=tools)["choices"][0]
    call = first["message"]["tool_calls"][0]
    messages += [first["message"], {"role": "tool", "tool_call_id": call["id"], "content": "1"}]
    second = chat(client, messages=messages, tools=tools)["choices"][0]

    assert first["finish_reason"] == "tool_calls"
    assert json.loads(call["function"]["arguments"])["sql"] == (
        "SELECT COUNT(*) AS row_count FROM invoice_lines"
    )
    assert (second["finish_reason"], second["message"].get("tool_calls")) == ("stop", None)


def test_without_tool_calls_the_loop_answers_at_once() -> None:
    client = TestClient(
        create_app(Timing(latency_ms=0, ttft_ms=0, tokens_per_s=0, tool_calls=False))
    )
    tools = [{"type": "function", "function": {"name": "search_knowledge", "parameters": {}}}]

    reply = chat(client, messages=[{"role": "user", "content": "q"}], tools=tools)

    assert reply["choices"][0]["finish_reason"] == "stop"


def test_an_error_rate_of_one_fails_every_call_and_stats_count_it() -> None:
    client = TestClient(create_app(Timing(latency_ms=0, error_rate=1.0)))

    response = client.post(
        "/v1/chat/completions", json={"messages": [{"role": "user", "content": "q"}]}
    )
    stats = client.get("/stats").json()

    assert response.status_code == 503
    assert (stats["errors"], stats["requests"]) == (1, {"complete": 1})


def test_a_plan_without_agents_is_direct() -> None:
    client = TestClient(create_app(INSTANT))
    schema = {"type": "object", "properties": {}}
    response_format = {"type": "json_schema", "json_schema": {"name": "PlanOut", "schema": schema}}

    reply = chat(client, messages=[{"role": "user", "content": "Question: hi"}],
                 response_format=response_format)  # fmt: skip

    assert json.loads(reply["choices"][0]["message"]["content"]) == {
        "mode": "direct",
        "subtasks": [],
    }


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        ({"type": "string", "minLength": 9}, "stubstubstub"),
        ({"type": "string", "maxLength": 2}, "st"),
        ({"type": "integer", "minimum": 3}, 3),
        ({"type": "number", "exclusiveMinimum": 0}, 1.0),
        ({"enum": ["b", "a"]}, "b"),
        ({"anyOf": [{"type": "string"}, {"type": "null"}]}, None),
        ({"type": "array", "items": {"type": "boolean"}, "minItems": 2}, [False, False]),
        ({"type": ["string", "null"]}, None),
        ({"type": "string", "format": "date"}, "2026-01-01"),
    ],
)
def test_minimal_instances(schema: dict[str, Any], expected: Any) -> None:
    assert instance(schema, schema) == expected


def test_instances_follow_references() -> None:
    root = {
        "type": "object",
        "properties": {
            "items": {"type": "array", "items": {"$ref": "#/$defs/Item"}, "minItems": 1}
        },
        "$defs": {"Item": {"type": "object", "properties": {"name": {"type": "string"}}}},
    }

    assert instance(root, root) == {"items": [{"name": "stub"}]}
