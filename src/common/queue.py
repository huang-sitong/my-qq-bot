"""内存消息队列适配器。

实现 ``domain.ports.MessageQueue``，作为默认的进程内队列实现。
后续可替换为 Kafka / Redis Stream 等 Broker 适配器。
"""

from __future__ import annotations

import asyncio
from typing import Any


class InMemoryMessageQueue:
    """基于 ``asyncio.Queue`` 的进程内消息队列。"""

    def __init__(self, maxsize: int = 0) -> None:
        self._queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=maxsize)

    async def put(self, item: Any) -> None:
        await self._queue.put(item)

    def put_nowait(self, item: Any) -> None:
        self._queue.put_nowait(item)

    async def get(self) -> Any:
        return await self._queue.get()

    def get_nowait(self) -> Any:
        return self._queue.get_nowait()

    def task_done(self) -> None:
        self._queue.task_done()

    async def join(self) -> None:
        await self._queue.join()

    def qsize(self) -> int:
        return self._queue.qsize()
