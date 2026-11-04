"""Owns the background tasks that produce answers, so they are not garbage-collected mid-run
and are cancelled cleanly on shutdown. Runs execute in the API process; the event protocol is
the same one long worker-side runs will use (ARCHITECTURE section 18)."""

import asyncio
from collections.abc import Coroutine
from typing import Any

from workmind.modules.conversations.events import RunEvents


class RunRegistry:
    def __init__(self, events: RunEvents) -> None:
        self.events = events
        self._tasks: set[asyncio.Task[None]] = set()

    def start(self, run: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
        task = asyncio.create_task(run)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def drain(self) -> None:
        """Waits for every running answer (tests, graceful shutdown)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def shutdown(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*list(self._tasks), return_exceptions=True)
