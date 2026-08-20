"""进程内领域事件总线适配器。

实现 ``domain.events.DomainEventBus``：按事件类型精确/继承匹配同步分发，
handler 异常只记日志不向发布方抛出——RAG 索引等旁路失败不得中断消息主流程。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from bot.package.domain.events import DomainEvent, DomainEventBus

logger = logging.getLogger(__name__)

EventHandler = Callable[[DomainEvent], Awaitable[None]]


class InMemoryDomainEventBus(DomainEventBus):
    """单进程、同事件循环内的轻量领域事件总线。"""

    def __init__(self) -> None:
        self._handlers: list[tuple[type[DomainEvent], EventHandler]] = []

    def subscribe(
        self,
        event_type: type[DomainEvent],
        handler: EventHandler,
    ) -> None:
        """订阅某类领域事件；继承该事件类型的子类事件也会被分发。"""
        if not isinstance(event_type, type) or not issubclass(event_type, DomainEvent):
            raise TypeError("event_type must be a DomainEvent subclass")
        self._handlers.append((event_type, handler))

    async def publish(self, event: DomainEvent) -> None:
        """向所有匹配的 handler 分发事件；handler 异常仅降级记录。"""
        if not isinstance(event, DomainEvent):
            raise TypeError("event must be a DomainEvent instance")
        handlers = [
            handler
            for event_type, handler in self._handlers
            if isinstance(event, event_type)
        ]
        if not handlers:
            return
        results = await asyncio.gather(
            *(handler(event) for handler in handlers),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException):
                logger.warning(
                    "Domain event handler failed: %s",
                    type(result).__name__,
                )


__all__ = ["InMemoryDomainEventBus"]
