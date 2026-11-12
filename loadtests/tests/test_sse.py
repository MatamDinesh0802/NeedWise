"""Reading a run's event stream: lines from chunks, events, and the timings people see."""

from collections.abc import Iterator

import pytest

from workmind_load.sse import Event, follow, lines_of, parse


def test_lines_survive_any_chunking_even_inside_a_character() -> None:
    body = 'event: token\ndata: {"text": "café"}\n\n: keep-alive\n\nlast'.encode()
    chunks = [body[i : i + 3] for i in range(0, len(body), 3)]

    assert list(lines_of(chunks)) == [
        "event: token",
        'data: {"text": "café"}',
        "",
        ": keep-alive",
        "",
        "last",
    ]


def test_events_comments_and_multi_line_data() -> None:
    lines = [
        "retry: 2000",
        "",
        "id: 1-0",
        "event: run.started",
        'data: {"run_id": "r"}',
        "",
        ": keep-alive",
        "",
        "data: line one",
        "data: line two",
        "",
    ]

    assert list(parse(lines)) == [
        None,
        Event("run.started", {"run_id": "r"}, "1-0"),
        None,
        Event("message", "line one\nline two"),
    ]


def sse(*events: tuple[str, str]) -> Iterator[str]:
    for name, data in events:
        yield f"event: {name}"
        yield f"data: {data}"
        yield ""


class Clock:
    """Each reading advances by the next step."""

    def __init__(self, *readings: float) -> None:
        self.readings = iter(readings)

    def __call__(self) -> float:
        return next(self.readings)


def test_a_completed_run_reports_sources_first_token_and_total() -> None:
    stream = sse(
        ("run.started", "{}"),
        ("retrieval", '{"sources": []}'),
        ("token", '{"text": "Leave "}'),
        ("token", '{"text": "is 16 weeks [S1]."}'),
        ("citation", '{"marker": "S1"}'),
        ("run.completed", '{"usage": {}}'),
    )

    timing = follow(
        stream, started=10.0, deadline_s=60, clock=Clock(10.1, 10.4, 11.0, 11.5, 11.6, 12.0)
    )

    assert timing.ok
    assert (timing.sources_s, timing.first_token_s, timing.total_s) == pytest.approx((0.4, 1, 2))
    assert (timing.tokens, timing.citations) == (2, 1)


def test_a_failed_run_says_why() -> None:
    stream = sse(("run.failed", '{"code": "model_unavailable", "message": "down"}'))

    timing = follow(stream, started=0.0, deadline_s=60, clock=Clock(0.5))

    assert (timing.status, timing.error, timing.total_s) == (
        "failed",
        "model_unavailable: down",
        0.5,
    )


def test_a_quiet_stream_times_out_on_its_keep_alives() -> None:
    lines = [": keep-alive", "", ": keep-alive", ""]

    timing = follow(lines, started=0.0, deadline_s=5, clock=Clock(3.0, 6.0))

    assert (timing.status, timing.first_token_s) == ("timeout", None)
    assert timing.error is not None


def test_a_stream_that_just_ends_is_not_complete() -> None:
    timing = follow(sse(("token", '{"text": "x"}')), started=0.0, deadline_s=5, clock=Clock(1.0))

    assert (timing.status, timing.ok, timing.first_token_s) == ("closed", False, 1.0)
