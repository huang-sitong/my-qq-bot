"""会话领域事件。

``ConversationTurnCompleted`` 在一个对话轮次结束时发布：它统一表示“本轮
消息已记录进会话（message recorded）且可选回复已发送（reply sent）”。
RAG 索引投影订阅该事件生成 ``IndexTurnTask``，dispatcher 不再手工拼装索引入队。
"""

from __future__ import annotations

from dataclasses import dataclass

from bot.package.conversation.record import MessageRecord
from bot.package.domain.events import DomainEvent


@dataclass(frozen=True)
class ConversationTurnCompleted(DomainEvent):
    """一轮会话已完成（消息已记录，bot 回复可为空）。"""

    thread_id: str
    messages: tuple[MessageRecord, ...]
    bot_id: str
    bot_name: str
    bot_reply: str

    def __post_init__(self) -> None:
        if not self.thread_id.strip():
            raise ValueError("thread_id must not be empty")
        if any(record.thread_id != self.thread_id for record in self.messages):
            raise ValueError("all message records must belong to event thread")

    @property
    def has_reply(self) -> bool:
        return bool(self.bot_reply.strip())


__all__ = ["ConversationTurnCompleted"]
