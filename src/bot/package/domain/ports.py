"""端口定义（Ports）。

核心流程依赖这些抽象接口，具体基础设施（asyncio.Queue、Kafka、Redis Stream、
Satori HTTP、Milvus 等）通过适配器实现。当前先定义消息队列、消息发送、RAG 索引
等端口，后续可继续补充记忆、视觉等外部依赖端口。
"""

from __future__ import annotations

from typing import Any, Protocol

from bot.package.domain.tasks import IndexTurnTask

__all__ = [
    "MessageQueue",
    "MessageSender",
    "RagIndexer",
    "UserMemoryStore",
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


class UserMemoryStore(Protocol):
    """用户长期记忆存储端口。"""

    async def load_memories(self, user_id: str) -> list[dict]: ...

    async def store_memory(self, user_id: str, key: str, value: str) -> None: ...

    async def delete_memory(self, user_id: str, key: str) -> None: ...

    async def clear_user_memories(self, user_id: str) -> None: ...

    async def format_memories(self, user_id: str) -> str: ...

    async def close(self) -> None: ...
