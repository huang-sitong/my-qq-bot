"""LangGraph 会话仓库适配器。

实现 ``domain.repositories.ConversationRepository``：把纯领域
``MessageRecord`` 翻译为 LangGraph checkpoint 能消费的 ``HumanMessage``，
并把清空会话上下文的命令翻译为 ``RemoveMessage`` 状态更新。

这是基础设施适配器，允许 import LangChain / LangGraph；领域层不感知这些类型。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from langchain_core.messages import HumanMessage, RemoveMessage

from bot.package.conversation import Conversation
from bot.package.orchestration.constants import EXTERNAL_UPDATE_NODE

if TYPE_CHECKING:
    from bot.package.conversation.record import MessageRecord

logger = logging.getLogger(__name__)


class LangGraphConversationRepository:
    """把 LangGraph checkpoint 暴露为领域会话仓库端口。"""

    def __init__(self, graph) -> None:
        self._graph = graph

    @staticmethod
    def _thread_config(thread_id: str) -> dict:
        return {"configurable": {"thread_id": thread_id}}

    async def append_record(
        self,
        record: MessageRecord,
        *,
        auto_reply: bool = False,
    ) -> None:
        """把一条纯领域消息记录追加为图外 HumanMessage 状态更新。

        消息归属与追加语义先经 ``Conversation`` 聚合根校验/执行，再做框架投影。
        """
        conversation = Conversation.restore(thread_id=record.thread_id)
        conversation = conversation.record_message(record)
        record = conversation.messages[-1]
        human = HumanMessage(
            content=record.context_text,
            name=record.user_name or None,
            additional_kwargs={
                "user_id": record.user_id,
                "user_name": record.user_name,
                "image_srcs": list(record.image_srcs),
                "auto_reply": auto_reply,
            },
        )
        await self._graph.aupdate_state(
            self._thread_config(record.thread_id),
            {"messages": [human]},
            as_node=EXTERNAL_UPDATE_NODE,
        )
        logger.debug(
            "ConversationRepository appended message %s to thread %s",
            record.message_id, record.thread_id,
        )

    async def clear(self, thread_id: str) -> None:
        """清空会话上下文，保留 persona；重置语义由聚合根定义。"""
        config = self._thread_config(thread_id)
        snapshot = await self._graph.aget_state(config)
        state = snapshot.values if snapshot is not None else {}
        messages = state.get("messages", [])
        conversation = Conversation.restore(
            thread_id=thread_id,
            bot_id=state.get("bot_id", ""),
            bot_name=state.get("bot_name", ""),
            conversation_summary=state.get("conversation_summary", ""),
            active_skills=tuple(state.get("active_skills", [])),
            tool_rounds=int(state.get("tool_rounds", 0)),
        )
        cleared = conversation.clear_context()
        updates = {
            "messages": [
                RemoveMessage(id=m.id) for m in messages if getattr(m, "id", None)
            ],
            "conversation_summary": cleared.conversation_summary,
            "active_skills": list(cleared.active_skills),
            "tool_rounds": cleared.tool_rounds,
        }
        await self._graph.aupdate_state(
            config,
            updates,
            as_node=EXTERNAL_UPDATE_NODE,
        )
        logger.info("ConversationRepository cleared thread %s", thread_id)


__all__ = ["LangGraphConversationRepository"]
