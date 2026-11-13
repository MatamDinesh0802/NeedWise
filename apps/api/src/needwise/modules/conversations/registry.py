"""Owns the background tasks that produce answers, so they are not garbage-collected mid-run
and are cancelled cleanly on shutdown. Runs execute in the API process; the event protocol is
the same one long worker-side runs will use (ARCHITECTURE section 18)."""

import asyncio
from collections.abc import Coroutine
from typing import Any

from needwise.core.context import RequestContext
from needwise.modules.conversations.events import RunEvents
from needwise.modules.conversations.inflight import InFlight


class RunRegistry:
    def __init__(self, events: RunEvents, inflight: InFlight) -> None:
        self.events = events
        self.inflight = inflight
        self._tasks: set[asyncio.Task[None]] = set()

    def start(
        self,
        run: Coroutine[Any, Any, None],
        *,
        slot: tuple[RequestContext, str] | None = None,
    ) -> asyncio.Task[None]:
        """`slot`: the in-flight slot (person, token) the run holds; freed when it ends."""
        if slot is not None:
            run = self._freeing(run, *slot)
        task = asyncio.create_task(run)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _freeing(
        self, run: Coroutine[Any, Any, None], ctx: RequestContext, token: str
    ) -> None:
        try:
            await run
        finally:
            await self.inflight.release(ctx, token)

    async def drain(self) -> None:
        """Waits for every running answer (tests, graceful shutdown)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def shutdown(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*list(self._tasks), return_exceptions=True)
