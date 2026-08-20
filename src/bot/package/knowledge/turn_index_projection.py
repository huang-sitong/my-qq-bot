"""会话轮次 RAG 索引投影。

订阅 ``ConversationTurnCompleted``，把领域事件翻译为逐条
``IndexTurnTask`` 并投递给 ``IndexWorker``。索引策略从 dispatcher 迁出：
谁产生领域事实（dispatcher）与谁决定如何投影（knowledge 上下文）解耦。
"""

from __future__ import annotations

import logging

from bot.package.conversation.content import IMAGE_PLACEHOLDER, MessageKind
from bot.package.conversation.events import ConversationTurnCompleted
from bot.package.domain import IndexTurnTask
from bot.package.utils import content_to_text

logger = logging.getLogger(__name__)


class TurnIndexProjection:
    """把已完成的会话轮次投影为 RAG 索引任务。"""

    def __init__(self, index_worker) -> None:
        self._index_worker = index_worker

    async def on_turn_completed(self, event: ConversationTurnCompleted) -> None:
        """对事件中每条用户消息生成一条索引任务。"""
        reply_text = content_to_text(event.bot_reply).strip()
        for record in event.messages:
            task = self._build_task(record, event, reply_text)
            if task is not None:
                await self._index_worker.enqueue(task)

    @staticmethod
    def _build_task(
        record,
        event: ConversationTurnCompleted,
        reply_text: str,
    ) -> IndexTurnTask | None:
        user_message = record.index_text
        if (
            record.content_kind == MessageKind.IMAGE.value
            and (user_message.strip() or reply_text)
        ):
            user_message = f"{user_message} {IMAGE_PLACEHOLDER}".strip()
        if not user_message.strip() and not reply_text:
            return None
        return IndexTurnTask(
            thread_id=record.thread_id,
            user_id=record.user_id,
            user_name=record.user_name,
            bot_id=event.bot_id,
            bot_name=event.bot_name,
            user_message=user_message,
            bot_reply=reply_text,
            trace_id=record.trace_id,
        )


__all__ = ["TurnIndexProjection"]
