"""Shared admission for actual model HTTP requests in one event loop."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager


class ModelSlots:
    def __init__(self, limit: int):
        if limit <= 0:
            raise ValueError("model request slot limit must be positive")
        self.limit = limit
        self.admit: Callable[[str | None], None] | None = None
        self._active = 0
        self._direct: deque[asyncio.Future[None]] = deque()
        self._normal: deque[asyncio.Future[None]] = deque()

    def _fill(self) -> None:
        while self._active < self.limit and (self._direct or self._normal):
            queue = self._direct if self._direct else self._normal
            waiter = queue.popleft()
            if waiter.cancelled():
                continue
            self._active += 1
            waiter.set_result(None)

    def _release(self) -> None:
        self._active -= 1
        self._fill()

    @asynccontextmanager
    async def slot(self, *, direct: bool = False, scene: str | None = None) -> AsyncIterator[None]:
        waiter: asyncio.Future[None] | None = None
        granted = False
        queue = self._direct if direct else self._normal
        if self._active < self.limit and not (self._direct or self._normal):
            self._active += 1
            granted = True
        else:
            waiter = asyncio.get_running_loop().create_future()
            queue.append(waiter)
            self._fill()
        try:
            if waiter is not None:
                await waiter
            if self.admit is not None:
                self.admit(scene)
            yield
        finally:
            # A waiter may be granted and then cancelled before its await resumes.
            if granted or (waiter is not None and waiter.done() and not waiter.cancelled()):
                self._release()
            elif waiter is not None and waiter in queue:
                queue.remove(waiter)
