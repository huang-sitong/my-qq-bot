"""领域仓库端口（Repository Ports）。

领域层只依赖这些抽象能力；SQLite / Milvus / LangGraph checkpoint 等具体
持久化设施通过适配器实现：

- ``ConversationRepository`` -> 编排层 ``LangGraphConversationRepository``
- ``DocumentRepository``  -> 知识层 ``DocumentStore``（milvus-lite）
- ``MemoryRepository``    -> 记忆层 ``MemoryStore``（SQLite / AsyncSqliteStore）

端口保持框架无关：这里不 import 任何持久化 / 工作流框架。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from bot.package.conversation.record import MessageRecord

__all__ = [
    "ConversationRepository",
    "DocumentRepository",
    "MemoryRepository",
]


class ConversationRepository(Protocol):
    """会话状态仓库端口。

    当前只暴露图外写入所需的两个最小能力；读侧/聚合状态方法在
    ``Conversation`` 聚合升级时按需补充，避免把 LangGraph 状态结构泄漏进领域层。
    """

    async def append_record(
        self,
        record: MessageRecord,
        *,
        auto_reply: bool = False,
    ) -> None: ...

    async def clear(self, thread_id: str) -> None: ...


class DocumentRepository(Protocol):
    """文档知识库仓库端口。

    ``DocumentStore`` 与测试中的轻量 fake 均按此结构实现。
    """

    async def add_texts(self, texts: list[str], metadatas: list[dict]) -> None: ...

    async def has_doc(self, file_hash: str) -> bool: ...

    async def delete_doc(self, doc_id: str) -> int: ...

    async def search_dense(
        self,
        query: str,
        expr: str,
        thread_id: str | None,
        k: int,
        output_fields: list[str] | None = None,
    ) -> list[dict]: ...

    async def search_sparse(
        self,
        query: str,
        expr: str,
        thread_id: str | None,
        k: int,
        output_fields: list[str] | None = None,
    ) -> list[dict]: ...

    def close(self) -> None: ...


class MemoryRepository(Protocol):
    """用户长期记忆仓库端口。"""

    async def load_memories(self, user_id: str) -> list[dict]: ...

    async def store_memory(self, user_id: str, key: str, value: str) -> None: ...

    async def delete_memory(self, user_id: str, key: str) -> None: ...

    async def clear_user_memories(self, user_id: str) -> None: ...

    async def format_memories(self, user_id: str) -> str: ...

    async def close(self) -> None: ...
