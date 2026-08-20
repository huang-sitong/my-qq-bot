"""平台接入层端口。

每个平台实现（当前仅 Satori）都需要提供事件源和发送能力；流水线与装配层只依赖
这里的窄接口，便于后续接入 OneBot / 官方 QQ WebSocket 等平台。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from bot.package.pipeline.pipeline import MessagePipeline


class EventSource(Protocol):
    """平台事件源。"""

    def on(self, event_type: str):
        """注册事件回调，返回装饰器。"""
        ...

    async def run(self) -> None: ...

    async def close(self) -> None: ...


class PlatformAdapter(Protocol):
    """平台适配器门面。"""

    def bind_pipeline(self, pipeline: MessagePipeline) -> None: ...

    def register_handlers(self) -> None: ...

    async def run(self) -> None: ...

    async def close(self) -> None: ...


__all__ = ["EventSource", "PlatformAdapter"]
