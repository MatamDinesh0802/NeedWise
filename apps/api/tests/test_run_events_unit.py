"""Streamed text as fewer `token` events (TokenBatcher, docs/benchmarks/load.md)."""

import asyncio
import time
import uuid
from typing import Any, cast

import pytest

from needwise.modules.conversations.events import RunEvents, RunMeta, TokenBatcher

RUN = RunMeta(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4())


class Recorder:
    """Stands in for RunEvents: records each emitted event and when it was written."""

    def __init__(self, *, write_s: float = 0.0, fail: bool = False) -> None:
        self.events: list[tuple[str, dict[str, Any], float]] = []
        self.write_s = write_s
        self.fail = fail

    async def emit(self, run: RunMeta, event: str, data: dict[str, Any]) -> None:
        assert run is RUN
        if self.write_s:
            await asyncio.sleep(self.write_s)
        if self.fail:
            raise ConnectionError("valkey is down")
        self.events.append((event, data, time.monotonic()))

    @property
    def texts(self) -> list[str]:
        return [data["text"] for name, data, _ in self.events if name == "token"]


def batcher(recorder: Recorder, interval_s: float = 0.05) -> TokenBatcher:
    return TokenBatcher(cast(RunEvents, recorder), RUN, interval_s=interval_s)


async def test_the_first_piece_goes_out_at_once_and_later_ones_together() -> None:
    recorder = Recorder()
    tokens = batcher(recorder, interval_s=10)  # nothing but close() sends after the first piece

    started = time.monotonic()
    await tokens.add("Parental ")
    first = recorder.events[0][2] - started
    for word in ("leave ", "is ", "16 ", "weeks ", "[S1]."):
        await tokens.add(word)
    before_close = list(recorder.texts)
    await tokens.close()

    assert first < 0.01
    assert before_close == ["Parental "]
    assert recorder.texts == ["Parental ", "leave is 16 weeks [S1]."]


async def test_waiting_text_is_sent_by_the_timer_without_another_piece() -> None:
    recorder = Recorder()
    tokens = batcher(recorder, interval_s=0.05)

    await tokens.add("a ")
    await tokens.add("b ")  # within the interval: waits
    await asyncio.sleep(0.15)  # no further piece arrives

    assert recorder.texts == ["a ", "b "]
    waited = recorder.events[1][2] - recorder.events[0][2]
    assert 0.04 <= waited < 0.12
    await tokens.close()
    assert recorder.texts == ["a ", "b "]  # nothing sent twice


async def test_a_steady_stream_is_cut_into_interval_sized_events_in_order() -> None:
    recorder = Recorder(write_s=0.002)  # writes take time, so timer and pieces interleave
    tokens = batcher(recorder, interval_s=0.03)
    words = [f"w{i} " for i in range(60)]

    for word in words:
        await tokens.add(word)
        await asyncio.sleep(0.005)  # 200 pieces per second, faster than the interval
    await tokens.close()

    assert "".join(recorder.texts) == "".join(words)
    assert 5 <= len(recorder.texts) <= 25  # far fewer events than the 60 pieces
    stamps = [at for _, _, at in recorder.events]
    assert stamps == sorted(stamps)


async def test_empty_pieces_send_nothing() -> None:
    recorder = Recorder()
    tokens = batcher(recorder)

    await tokens.add("")
    await tokens.close()

    assert recorder.events == []


async def test_a_failed_timed_write_is_raised_by_the_next_call() -> None:
    recorder = Recorder()
    tokens = batcher(recorder, interval_s=0.02)
    await tokens.add("a ")
    recorder.fail = True
    await tokens.add("b ")  # waits for the timer, whose write fails
    await asyncio.sleep(0.08)

    with pytest.raises(ConnectionError):
        await tokens.add("c ")


async def test_close_can_stay_quiet_for_a_run_that_is_already_failing() -> None:
    recorder = Recorder()
    tokens = batcher(recorder, interval_s=10)
    await tokens.add("a ")
    await tokens.add("b ")
    recorder.fail = True

    await tokens.close(quiet=True)
    await tokens.add("c ")  # within the interval: waits
    with pytest.raises(ConnectionError):
        await tokens.close()

    assert recorder.texts == ["a "]


async def test_cancel_drops_the_timer_without_sending() -> None:
    recorder = Recorder()
    tokens = batcher(recorder, interval_s=0.02)
    await tokens.add("a ")
    await tokens.add("b ")

    tokens.cancel()
    await asyncio.sleep(0.06)

    assert recorder.texts == ["a "]
