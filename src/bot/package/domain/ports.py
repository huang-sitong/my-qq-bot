"""端口定义（Ports）。

核心流程依赖这些抽象接口，具体基础设施（asyncio.Queue、Kafka、Redis Stream、
Satori HTTP、Milvus 等）通过适配器实现。当前定义消息队列、消息发送、RAG 索引等流程端口；仓库端口统一见
``domain.repositories``。

MessageRouter / MessageSink / ContextCompactorPort 原位于 pipeline.contracts，
该兼容垫片已删除：此处（``bot.package.domain.ports``）是这些端口的唯一源，
流水线（worker/dispatcher）直接按这些协议消费。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from bot.package.domain.repositories import MemoryRepository
from bot.package.domain.tasks import IndexTurnTask

if TYPE_CHECKING:
    from bot.package.conversation.message import IncomingMessage
    from bot.package.conversation.router import RouteDecision

__all__ = [
    "ContextCompactorPort",
    "MemoryRepository",
    "MessageQueue",
    "MessageRouter",
    "MessageSender",
    "MessageSink",
    "RagIndexer",
    "VisionServicePort",
]


class MessageQueue(Protocol):
    """异步消息队列的最小端口。

    与 ``asyncio.Queue`` 的方法签名对齐，便于直接使用 asyncio.Queue 作为默认适配器；
    后续可替换为基于 Broker 的实现。
    """

    async def put(self, item: Any) -> None: ...

    def put_nowait(self, item: Any) -> None: ...

    async def get(self) -> Any: ...

    def get_nowait(self) -> Any: ...

    def task_done(self) -> None: ...

    async def join(self) -> None: ...

    def qsize(self) -> int: ...


class MessageSender(Protocol):
    """向聊天平台发送消息/文件的端口。"""

    async def send_message(self, channel_id: str, content: str) -> None: ...

    async def send_file(
        self,
        channel_id: str,
        local_path: str,
        final_name: str | None = None,
    ) -> Any: ...


class RagIndexer(Protocol):
    """RAG 后台索引队列端口。"""

    async def enqueue(self, task: IndexTurnTask) -> bool: ...


class VisionServicePort(Protocol):
    """视觉理解服务端口。"""

    async def describe(self, src: str) -> str: ...

    async def describe_many(self, srcs: list[str]) -> list[str]: ...

    async def close(self) -> None: ...




class MessageRouter(Protocol):
    """把归一化消息路由为 ``RouteDecision``。"""

    def __call__(self, message: IncomingMessage, **opts: Any) -> RouteDecision: ...


class MessageSink(Protocol):
    """消费路由决策并执行对应动作。

    单条调用走 :meth:`dispatch`；突发合并批走 :meth:`dispatch_batch`
    （整批一次图调用、一条回复，RAG 索引逐条入队）。
    """

    async def dispatch(
        self,
        message: IncomingMessage,
        decision: RouteDecision,
        *,
        auto_reply_allowed: bool = False,
    ) -> None: ...

    async def dispatch_batch(
        self,
        messages: list[IncomingMessage],
        decisions: list[RouteDecision],
        *,
        auto_reply_flags: list[bool] | None = None,
    ) -> None: ...


class ContextCompactorPort(Protocol):
    """图外上下文压缩端口。"""

    async def compact_if_needed(self, thread_id: str) -> int: ...
