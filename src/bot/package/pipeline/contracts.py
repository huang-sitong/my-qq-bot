"""事件流水线端口定义。

流水线只依赖这些窄接口和共享领域对象，平台接入层与装配层分别实现/注入。
"""

from __future__ import annotations

from typing import Any, Protocol

from bot.package.conversation.message import IncomingMessage
from bot.package.conversation.router import RouteDecision


class MessageRouter(Protocol):
    """把归一化消息路由为 ``RouteDecision``。"""

    def __call__(self, message: IncomingMessage, **opts: Any) -> RouteDecision: ...


class MessageSink(Protocol):
    """消费一条路由决策并执行对应动作。"""

    async def dispatch(
        self,
        message: IncomingMessage,
        decision: RouteDecision,
        *,
        auto_reply_allowed: bool = False,
    ) -> None: ...


class ContextCompactorPort(Protocol):
    """图外上下文压缩端口。"""

    async def compact_if_needed(self, thread_id: str) -> int: ...


__all__ = ["ContextCompactorPort", "MessageRouter", "MessageSink"]
