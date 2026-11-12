"""A stand-in model server for load tests: speaks the OpenAI-compatible API that WorkMind's
`openai_compatible` provider uses (apps/api/src/workmind/providers/llm.py), answers instantly
in content but with configurable timing, and costs nothing.

    uv run python stub_llm.py --port 8490 --ttft-ms 600 --tokens-per-s 60

Then point the stack at http://host.docker.internal:8490/v1 (docs/load-testing.md).

What it answers, deterministically (the same request always gets the same text):

- POST /v1/chat/completions, the four shapes the adapter sends:
  - streamed (`stream: true`): SSE chunks, one word per chunk, then a usage chunk and
    `data: [DONE]`. An answer prompt with <source id="S1" ...> elements gets one sentence per
    source (up to three), each cited as [S1], padded to `--answer-tokens` words.
  - plain: a question rewrite ("Latest message: ...") comes back unchanged, a conversation
    summary as one sentence, anything else as a short cited answer or "OK".
  - structured (`response_format.json_schema`): a plan for the agents planner (PlanOut, picking
    agents from the prompt's list by word overlap with the question), findings that cite the
    prompt's sources (EvidenceOut), an analytics answer that cites [Q1] (Composed), and a
    minimal valid instance of any other schema.
  - tools: on the first turn, a call to `search_knowledge` (research mode) or `run_sql` with a
    COUNT(*) over the first table of the analytics data model; after a tool result, a final
    answer. Other tools are never called.
- GET /v1/models, GET /healthz, and GET /stats (requests by kind, in flight, peak).

Timing: every response waits `--latency-ms` before it starts. A stream then waits `--ttft-ms`
for its first token and sends `--tokens-per-s` words per second. A non-streamed reply arrives
after latency + ttft + its words / tokens-per-s, as if the model had generated it in full.
`--jitter` varies each delay by up to that fraction (seeded, so runs are repeatable).
`--error-rate` answers that fraction of requests with HTTP 503.

It has no authentication and no limits: bind it to 127.0.0.1 (the default) unless the stack
cannot reach it otherwise. Needs only Starlette and Uvicorn (both in apps/api and loadtests).
"""

import argparse
import asyncio
import html
import json
import random
import re
import time
from collections import Counter
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

MODEL = "workmind-stub"
ABSTAIN = "I couldn't find this in the sources available to you."
PADDING = (
    "This answer was written by the WorkMind load-test stub, which repeats what the sources "
    "say without reading them closely."
)
MAX_CITED_SOURCES = 3
MAX_FINDINGS = 4
MAX_SENTENCE_CHARS = 160

_SOURCE = re.compile(r"<source\s+([^>]*)>(.*?)</source>", re.DOTALL)
_ATTRIBUTE = re.compile(r'(\w+)="([^"]*)"')
_AGENT_LINE = re.compile(r'^- "([^"]+)": (.*)$', re.MULTILINE)
_RESULT = re.compile(r'<result query="(Q\d+)"[^>]*>\n(.*?)</result>', re.DOTALL)
_TABLES = re.compile(r"^Tables \(use these names in run_sql\):\n- ([A-Za-z_][A-Za-z0-9_]*) \(", re.M)
_WORD = re.compile(r"[a-z0-9]+")
_TOKEN = re.compile(r"\S+\s*")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s")
_MARKUP = re.compile(r"[#*_`|>\[\]]+")
_STOPWORDS = frozenset(
    "what when where which who whom whose why how does did do the and for with from that this "
    "have has our are was were will your about into over under than then them they their there "
    "is it its of on in to a an or be by at as we i my me can".split()
)


@dataclass(frozen=True)
class Timing:
    latency_ms: float = 20.0
    ttft_ms: float = 600.0
    tokens_per_s: float = 60.0
    answer_tokens: int = 120
    jitter: float = 0.0
    error_rate: float = 0.0
    tool_calls: bool = True
    seed: int = 0


@dataclass
class Stats:
    requests: Counter[str] = field(default_factory=Counter[str])
    errors: int = 0
    in_flight: int = 0
    peak_in_flight: int = 0
    started: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        return {
            "requests": dict(self.requests),
            "total": sum(self.requests.values()),
            "errors": self.errors,
            "in_flight": self.in_flight,
            "peak_in_flight": self.peak_in_flight,
            "uptime_s": round(time.time() - self.started, 1),
        }


# --- Reading the request --------------------------------------------------------------------


def text_of(content: Any) -> str:
    """A message's text: a string, or OpenAI content parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:  # pyright: ignore[reportUnknownVariableType]
            if isinstance(part, dict) and isinstance(part.get("text"), str):  # pyright: ignore[reportUnknownMemberType]
                parts.append(str(part["text"]))  # pyright: ignore[reportUnknownArgumentType]
        return "\n".join(parts)
    return ""


def messages_of(body: dict[str, Any]) -> list[dict[str, Any]]:
    raw = body.get("messages")
    return [m for m in raw if isinstance(m, dict)] if isinstance(raw, list) else []  # pyright: ignore[reportUnknownVariableType]


def system_of(messages: list[dict[str, Any]]) -> str:
    return "\n".join(text_of(m.get("content")) for m in messages if m.get("role") == "system")


def last_user(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return text_of(message.get("content"))
    return ""


def question_of(user: str) -> str:
    """The question in an answer, planner or agent prompt ("Question: ...", "Goal: ..."), else
    the whole message."""
    for label in ("Question: ", "Goal: ", "Latest message: "):
        index = user.rfind(label)
        if index >= 0:
            return user[index + len(label) :].strip().splitlines()[0].strip()
    return user.strip()


@dataclass(frozen=True)
class Source:
    marker: str
    title: str
    text: str


def sources_in(text: str) -> list[Source]:
    found: list[Source] = []
    for attributes, body in _SOURCE.findall(text):
        attrs = dict(_ATTRIBUTE.findall(attributes))
        marker = attrs.get("id", "")
        if marker:
            found.append(Source(marker, html.unescape(attrs.get("title", "")), html.unescape(body)))
    return found


def first_sentence(text: str) -> str:
    flat = " ".join(_MARKUP.sub(" ", text).split())
    sentence = _SENTENCE_END.split(flat, maxsplit=1)[0] if flat else ""
    sentence = sentence[:MAX_SENTENCE_CHARS].rstrip(" .,;:!?")
    return sentence or "The source covers this"


def words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if len(w) > 2 and w not in _STOPWORDS}


# --- Writing the reply ----------------------------------------------------------------------


def cited_answer(sources: list[Source], *, pad_to: int = 0) -> str:
    """One sentence per source, each cited, padded to `pad_to` words."""
    if not sources:
        return ABSTAIN
    sentences = [f"{first_sentence(s.text)} [{s.marker}]." for s in sources[:MAX_CITED_SOURCES]]
    text = "According to the sources: " + " ".join(sentences)
    padding = PADDING.split()
    extra = [padding[i % len(padding)] for i in range(max(0, pad_to - len(text.split())))]
    if extra:
        text += " " + " ".join(extra).rstrip(".,") + "."
    return text


def plain_reply(messages: list[dict[str, Any]], pad_to: int) -> str:
    system, user = system_of(messages), last_user(messages)
    if "Latest message: " in user:  # rewrite_question: the message is already standalone
        return question_of(user)
    if system.startswith("Summarize this conversation"):
        return "The employee asked the assistant about company documents; it answered from them."
    sources = sources_in(user)
    if sources or "<sources>" in user:
        return cited_answer(sources, pad_to=pad_to)
    return "OK"


def plan(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """The agents planner's PlanOut: agents whose description shares words with the question."""
    agents = _AGENT_LINE.findall(system_of(messages))
    question = question_of(last_user(messages))
    asked = words(question)
    scored = [(len(asked & words(f"{key} {description}")), i, key) for i, (key, description) in enumerate(agents)]
    ranked = [key for score, _, key in sorted(scored, key=lambda s: (-s[0], s[1])) if score > 0]
    if not agents or (not ranked and asked <= {"hello", "thanks", "thank", "hey"}):
        return {"mode": "direct", "subtasks": []}
    if len(ranked) >= 2 and " and " in f" {question.lower()} ":
        chosen = ranked[:2]
    else:
        chosen = ranked[:1] or [agents[0][0]]
    return {
        "mode": "fan_out" if len(chosen) > 1 else "single",
        "subtasks": [{"agent": key, "goal": question} for key in chosen],
    }


def evidence(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """A department agent's EvidenceOut: one finding per source it was given."""
    sources = sources_in(last_user(messages))
    return {
        "findings": [
            {"claim": first_sentence(s.text) + ".", "sources": [s.marker]}
            for s in sources[:MAX_FINDINGS]
        ],
        "gaps": [] if sources else [question_of(last_user(messages))],
        "follow_up_query": "",
    }


def composed(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """The analytics agent's Composed answer: cites the first result and copies one of its
    values verbatim, so number verification can check it."""
    results = _RESULT.findall(last_user(messages))
    if not results:
        return {
            "narrative": "No query returned a result, so the data does not answer this question.",
            "chart": None,
            "main_query": None,
        }
    query, body = results[0]
    lines = body.strip().splitlines()
    columns = [c.rsplit(" (", 1)[0] for c in lines[0].removeprefix("columns: ").split(" | ")]
    first_row = lines[1].split(" | ") if len(lines) > 1 else []
    narrative = f"The query results answer the question [{query}]."
    if first_row:
        narrative += f" The first row reads {first_row[-1]} for {columns[-1]} [{query}]."
    chart = {
        "type": "table",
        "query": query,
        "x": columns[0],
        "y": [columns[-1]],
        "series": None,
        "title": "Query result",
        "y_format": "number",
    }
    # As the prompt asks: no chart for a single value (one column).
    return {"narrative": narrative, "chart": chart if len(columns) > 1 else None, "main_query": query}


def instance(schema: dict[str, Any], root: dict[str, Any], depth: int = 0) -> Any:
    """A minimal value that satisfies `schema` (strict structured-output schemas: every property
    is present; nullable ones are null)."""
    if depth > 20:
        return None
    if "$ref" in schema:
        name = str(schema["$ref"]).rsplit("/", 1)[-1]
        return instance(root.get("$defs", {}).get(name, {}), root, depth + 1)
    if "const" in schema:
        return schema["const"]
    if schema.get("enum"):
        return schema["enum"][0]
    options = schema.get("anyOf") or schema.get("oneOf")
    if options:
        if any(o.get("type") == "null" for o in options):
            return None
        return instance(options[0], root, depth + 1)
    kind = schema.get("type")
    if isinstance(kind, list):
        kinds = [str(k) for k in kind]  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
        if "null" in kinds:
            return None
        kind = kinds[0] if kinds else None
    if kind == "object":
        properties: dict[str, Any] = schema.get("properties") or {}
        return {name: instance(sub, root, depth + 1) for name, sub in properties.items()}
    if kind == "array":
        count = int(schema.get("minItems") or 0)
        return [instance(schema.get("items") or {}, root, depth + 1) for _ in range(count)]
    if kind == "integer":
        return int(schema.get("minimum", schema.get("exclusiveMinimum", -1) + 1))
    if kind == "number":
        return float(schema.get("minimum", schema.get("exclusiveMinimum", -1) + 1))
    if kind == "boolean":
        return False
    if kind == "null":
        return None
    if schema.get("format") == "date":
        return "2026-01-01"
    if schema.get("format") == "date-time":
        return "2026-01-01T00:00:00Z"
    text = "stub"
    minimum = int(schema.get("minLength") or 0)
    if len(text) < minimum:
        text = text * (minimum // len(text) + 1)
    maximum = schema.get("maxLength")
    return text[: int(maximum)] if maximum is not None else text


def structured(body: dict[str, Any], messages: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    spec = body["response_format"].get("json_schema") or {}
    name = str(spec.get("name") or "")
    schema: dict[str, Any] = spec.get("schema") or {}
    if name == "PlanOut":
        return name, plan(messages)
    if name == "EvidenceOut":
        return name, evidence(messages)
    if name == "Composed":
        return name, composed(messages)
    value = instance(schema, schema)
    return name or "schema", value if isinstance(value, dict) else {"value": value}


def tool_turn(body: dict[str, Any], messages: list[dict[str, Any]], timing: Timing) -> dict[str, Any]:
    """The assistant message of one tool-loop turn."""
    offered = {
        str(t["function"]["name"])
        for t in body.get("tools") or []
        if isinstance(t, dict) and isinstance(t.get("function"), dict)  # pyright: ignore[reportUnknownMemberType]
    }
    answered = any(m.get("role") == "tool" for m in messages)
    question = question_of(last_user(messages))
    call: tuple[str, dict[str, Any]] | None = None
    if timing.tool_calls and not answered:
        if "search_knowledge" in offered:
            call = ("search_knowledge", {"query": question[:500] or "company policy"})
        elif "run_sql" in offered and (table := _TABLES.search(system_of(messages))):
            sql = f"SELECT COUNT(*) AS row_count FROM {table.group(1)}"
            call = ("run_sql", {"sql": sql, "purpose": f"Count the rows of {table.group(1)}"})
    if call is None:
        results = [text_of(m.get("content")) for m in messages if m.get("role") == "tool"]
        text = "I looked this up with the tools." if results else "No tool was needed."
        return {"role": "assistant", "content": text}
    turn = sum(1 for m in messages if m.get("role") == "assistant")
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": f"call_{turn + 1}",
                "type": "function",
                "function": {"name": call[0], "arguments": json.dumps(call[1])},
            }
        ],
    }


def prompt_tokens(messages: list[dict[str, Any]]) -> int:
    """About four characters per token, as for English text."""
    return max(1, sum(len(text_of(m.get("content"))) for m in messages) // 4)


def completion_tokens(text: str) -> int:
    return max(1, len(_TOKEN.findall(text)))


# --- The app ----------------------------------------------------------------------------------


class Stub:
    def __init__(self, timing: Timing) -> None:
        self.timing = timing
        self.stats = Stats()
        self._random = random.Random(timing.seed)  # noqa: S311  (timing jitter, not security)

    def delay(self, seconds: float) -> float:
        if self.timing.jitter <= 0 or seconds <= 0:
            return max(0.0, seconds)
        spread = self.timing.jitter * seconds
        return max(0.0, seconds + self._random.uniform(-spread, spread))

    def generation_s(self, tokens: int) -> float:
        rate = self.timing.tokens_per_s
        return tokens / rate if rate > 0 else 0.0

    async def chat(self, request: Request) -> Response:
        try:
            body = await request.json()
        except ValueError:
            return _error(400, "invalid_request_error", "The body is not JSON.")
        if not isinstance(body, dict):
            return _error(400, "invalid_request_error", "The body must be a JSON object.")
        payload: dict[str, Any] = body  # pyright: ignore[reportUnknownVariableType]
        messages = messages_of(payload)
        if not messages:
            return _error(400, "invalid_request_error", "messages is required.")
        kind = (
            "tools"
            if payload.get("tools")
            else "structured"
            if isinstance(payload.get("response_format"), dict)
            else "stream"
            if payload.get("stream")
            else "complete"
        )
        self.stats.requests[kind] += 1
        if self.timing.error_rate > 0 and self._random.random() < self.timing.error_rate:
            self.stats.errors += 1
            await asyncio.sleep(self.delay(self.timing.latency_ms / 1000))
            return _error(503, "overloaded_error", "The stub is configured to fail sometimes.")
        model = str(payload.get("model") or MODEL)
        if kind == "stream":
            return StreamingResponse(
                self._stream(model, messages, payload),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache"},
            )
        if kind == "tools":
            message = tool_turn(payload, messages, self.timing)
            reason = "tool_calls" if message.get("tool_calls") else "stop"
            text = message.get("content") or json.dumps(message.get("tool_calls"))
        elif kind == "structured":
            name, value = structured(payload, messages)
            self.stats.requests[f"structured:{name}"] += 1
            text = json.dumps(value)
            message = {"role": "assistant", "content": text}
            reason = "stop"
        else:
            text = plain_reply(messages, self.timing.answer_tokens if _answering(messages) else 0)
            message = {"role": "assistant", "content": text}
            reason = "stop"
        tokens = completion_tokens(str(text))
        wait = self.timing.latency_ms / 1000 + self.timing.ttft_ms / 1000
        await self._hold(self.delay(wait + self.generation_s(tokens)))
        return JSONResponse(
            {
                "id": f"chatcmpl-stub-{self.stats.requests.total()}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "message": message, "finish_reason": reason}],
                "usage": _usage(prompt_tokens(messages), tokens),
            }
        )

    async def _hold(self, seconds: float) -> None:
        self.stats.in_flight += 1
        self.stats.peak_in_flight = max(self.stats.peak_in_flight, self.stats.in_flight)
        try:
            await asyncio.sleep(seconds)
        finally:
            self.stats.in_flight -= 1

    async def _stream(
        self, model: str, messages: list[dict[str, Any]], payload: dict[str, Any]
    ) -> AsyncIterator[str]:
        text = plain_reply(messages, self.timing.answer_tokens)
        tokens = _TOKEN.findall(text)
        created = int(time.time())
        ident = f"chatcmpl-stub-{self.stats.requests.total()}"

        def chunk(delta: dict[str, Any], reason: str | None = None) -> str:
            data = {
                "id": ident,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": reason}],
            }
            return f"data: {json.dumps(data)}\n\n"

        self.stats.in_flight += 1
        self.stats.peak_in_flight = max(self.stats.peak_in_flight, self.stats.in_flight)
        try:
            await asyncio.sleep(self.delay((self.timing.latency_ms + self.timing.ttft_ms) / 1000))
            yield chunk({"role": "assistant", "content": ""})
            started = time.monotonic()
            interval = 1 / self.timing.tokens_per_s if self.timing.tokens_per_s > 0 else 0.0
            for index, token in enumerate(tokens):
                due = started + index * interval
                if (wait := due - time.monotonic()) > 0:
                    await asyncio.sleep(wait)
                yield chunk({"content": token})
            yield chunk({}, "stop")
            options = payload.get("stream_options")
            if isinstance(options, dict) and options.get("include_usage"):  # pyright: ignore[reportUnknownMemberType]
                usage = _usage(prompt_tokens(messages), len(tokens))
                data = {"id": ident, "object": "chat.completion.chunk", "created": created,
                        "model": model, "choices": [], "usage": usage}  # fmt: skip
                yield f"data: {json.dumps(data)}\n\n"
            yield "data: [DONE]\n\n"
        finally:
            self.stats.in_flight -= 1

    async def models(self, request: Request) -> Response:
        return JSONResponse({"object": "list", "data": [{"id": MODEL, "object": "model"}]})

    async def health(self, request: Request) -> Response:
        return JSONResponse({"status": "ok"})

    async def stats_view(self, request: Request) -> Response:
        return JSONResponse({**self.stats.as_dict(), "timing": self.timing.__dict__})


def _answering(messages: list[dict[str, Any]]) -> bool:
    return "<sources>" in last_user(messages)


def _usage(prompt: int, completion: int) -> dict[str, int]:
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }


def _error(status: int, kind: str, message: str) -> Response:
    return JSONResponse({"error": {"message": message, "type": kind, "code": kind}}, status)


def create_app(timing: Timing | None = None) -> Starlette:
    stub = Stub(timing or Timing())
    app = Starlette(
        routes=[
            Route("/v1/chat/completions", stub.chat, methods=["POST"]),
            Route("/chat/completions", stub.chat, methods=["POST"]),
            Route("/v1/models", stub.models, methods=["GET"]),
            Route("/healthz", stub.health, methods=["GET"]),
            Route("/stats", stub.stats_view, methods=["GET"]),
        ]
    )
    app.state.stub = stub
    return app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="WorkMind load-test model stub")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8490)
    parser.add_argument("--latency-ms", type=float, default=Timing.latency_ms)
    parser.add_argument("--ttft-ms", type=float, default=Timing.ttft_ms)
    parser.add_argument("--tokens-per-s", type=float, default=Timing.tokens_per_s)
    parser.add_argument("--answer-tokens", type=int, default=Timing.answer_tokens)
    parser.add_argument("--jitter", type=float, default=Timing.jitter)
    parser.add_argument("--error-rate", type=float, default=Timing.error_rate)
    parser.add_argument("--no-tool-calls", action="store_true")
    parser.add_argument("--seed", type=int, default=Timing.seed)
    args = parser.parse_args(argv)
    timing = Timing(
        latency_ms=args.latency_ms,
        ttft_ms=args.ttft_ms,
        tokens_per_s=args.tokens_per_s,
        answer_tokens=args.answer_tokens,
        jitter=args.jitter,
        error_rate=args.error_rate,
        tool_calls=not args.no_tool_calls,
        seed=args.seed,
    )
    print(f"WorkMind model stub on http://{args.host}:{args.port}/v1 ({timing})", flush=True)
    uvicorn.run(
        create_app(timing),
        host=args.host,
        port=args.port,
        log_level="warning",
        backlog=4096,
        timeout_keep_alive=30,
    )


if __name__ == "__main__":
    main()
