"""Reading a run's Server-Sent Events (GET /api/v1/runs/{id}/events, ARCHITECTURE 18).

The clock starts when the question is sent (POST .../messages), as a person sees it:

    time to first token   until the first `token` event (what the person waits for)
    time to sources       until the `retrieval` event (search done, model not yet answering)
    answer complete       until `run.completed`

A run ends with run.completed, run.failed or run.cancelled; the server sends a keep-alive
comment every few seconds while nothing happens, so a deadline is checked at least that often.
"""

import codecs
import json
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

TERMINAL = {"run.completed": "completed", "run.failed": "failed", "run.cancelled": "cancelled"}


@dataclass(frozen=True)
class Event:
    event: str
    data: Any
    id: str | None = None


def lines_of(chunks: Iterable[bytes]) -> Iterator[str]:
    """Body chunks (as they arrive) -> lines. SSE is UTF-8; a character may span two chunks."""
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    pending = ""
    for chunk in chunks:
        pending += decoder.decode(chunk)
        *complete, pending = pending.split("\n")
        yield from complete
    pending += decoder.decode(b"", final=True)
    if pending:
        yield pending


def parse(lines: Iterable[str]) -> Iterable[Event | None]:
    """SSE lines -> events. Yields None for a comment or retry line (a keep-alive), so callers
    can check a deadline while the stream is quiet."""
    name, data, ident = "message", list[str](), None
    for raw in lines:
        line = raw.rstrip("\r")
        if not line:
            if data:
                text = "\n".join(data)
                try:
                    payload: Any = json.loads(text)
                except ValueError:
                    payload = text
                yield Event(name, payload, ident)
            name, data, ident = "message", [], None
            continue
        if line.startswith(":") or line.startswith("retry:"):
            yield None
            continue
        field_name, _, value = line.partition(":")
        value = value.removeprefix(" ")
        if field_name == "event":
            name = value
        elif field_name == "data":
            data.append(value)
        elif field_name == "id":
            ident = value


@dataclass
class RunTiming:
    status: str = "closed"  # completed | failed | cancelled | timeout | closed (stream ended)
    first_token_s: float | None = None
    sources_s: float | None = None
    total_s: float | None = None
    tokens: int = 0
    citations: int = 0
    events: dict[str, int] = field(default_factory=dict[str, int])
    error: str | None = None  # run.failed's code and message

    @property
    def ok(self) -> bool:
        return self.status == "completed"


def follow(
    lines: Iterable[str],
    *,
    started: float,
    deadline_s: float,
    clock: Callable[[], float] = time.perf_counter,
) -> RunTiming:
    """Reads the stream until the run ends or `deadline_s` after `started` (perf_counter)."""
    timing = RunTiming()
    for event in parse(lines):
        now = clock()
        if event is not None:
            timing.events[event.event] = timing.events.get(event.event, 0) + 1
            if event.event == "token":
                timing.tokens += 1
                if timing.first_token_s is None:
                    timing.first_token_s = now - started
            elif event.event == "retrieval" and timing.sources_s is None:
                timing.sources_s = now - started
            elif event.event == "citation":
                timing.citations += 1
            elif event.event in TERMINAL:
                timing.status = TERMINAL[event.event]
                timing.total_s = now - started
                if event.event == "run.failed" and isinstance(event.data, dict):
                    code = event.data.get("code", "failed")  # pyright: ignore[reportUnknownMemberType]
                    message = event.data.get("message", "")  # pyright: ignore[reportUnknownMemberType]
                    timing.error = f"{code}: {message}"
                return timing
        if now - started > deadline_s:
            timing.status = "timeout"
            timing.error = f"no end of run within {deadline_s:.0f} s"
            return timing
    return timing
