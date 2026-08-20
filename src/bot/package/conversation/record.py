"""会话消息记录（MessageRecord）— 框架无关的纯领域值对象。

与 LangChain ``HumanMessage`` / ``AIMessage`` 解耦：领域层只描述“一条进入
会话的消息”的事实（供聚合、策略与后续领域事件使用），不关心它如何被
LangGraph 持久化或渲染。``context_text`` 是注入 LLM 的上下文文本，
``index_text`` 是用于 RAG/检索的干净文本。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from bot.package.conversation.message import IncomingMessage

MessageRole = Literal["user", "assistant"]

_VALID_ROLES = frozenset({"user", "assistant"})


@dataclass(frozen=True)
class MessageRecord:
    """会话中的一条消息记录。"""

    message_id: str
    thread_id: str
    user_id: str
    user_name: str
    context_text: str
    index_text: str = ""
    role: MessageRole = "user"
    created_at: str = ""
    image_srcs: tuple[str, ...] = ()
    trace_id: str = ""
    content_kind: str = "text"

    def __post_init__(self) -> None:
        if not self.message_id.strip():
            raise ValueError("message_id must not be empty")
        if not self.thread_id.strip():
            raise ValueError("thread_id must not be empty")
        if self.role not in _VALID_ROLES:
            raise ValueError(f"role must be one of {sorted(_VALID_ROLES)}")

    @property
    def is_user(self) -> bool:
        return self.role == "user"

    @property
    def is_assistant(self) -> bool:
        return self.role == "assistant"

    @classmethod
    def from_incoming(
        cls,
        message: IncomingMessage,
        *,
        role: MessageRole = "user",
        created_at: str = "",
    ) -> MessageRecord:
        """把协议归一化后的入站消息转换为纯领域消息记录。"""
        return cls(
            message_id=message.event_id,
            thread_id=message.thread_id,
            user_id=message.user_id,
            user_name=message.user_name,
            context_text=message.llm_text,
            index_text=message.clean_text,
            role=role,
            created_at=created_at,
            image_srcs=tuple(message.image_srcs),
            trace_id=message.trace_id,
            content_kind=message.content_kind,
        )


__all__ = ["MessageRecord", "MessageRole"]
