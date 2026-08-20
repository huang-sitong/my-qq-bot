"""领域事件与事件总线端口。

领域对象只发布 ``DomainEvent``；具体进程内/跨进程总线由基础设施适配器实现。
当前使用进程内同步分发（``utils.event_bus.InMemoryDomainEventBus``）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class DomainEvent:
    """领域事件基类标记。"""


class DomainEventBus(Protocol):
    """领域事件总线端口。"""

    def subscribe(
        self,
        event_type: type[DomainEvent],
        handler: Callable[[DomainEvent], Awaitable[None]],
    ) -> None: ...

    async def publish(self, event: DomainEvent) -> None: ...


__all__ = ["DomainEvent", "DomainEventBus"]
