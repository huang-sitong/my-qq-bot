"""rag_tool_node — 执行 call_llm 请求的工具调用。

读取 state 最后一条消息的 tool_calls，逐条执行 search_chat_history，
把结果以 ToolMessage 写回 state。失败仅降级为占位文案，不中断对话。
"""

import logging

from langchain_core.messages import ToolMessage

from bot.core.tools import search_chat_history
from object.bot.state import BotState

logger = logging.getLogger(__name__)


async def rag_tool_node(state: BotState, rag_service=None) -> dict:
    messages = state.get("messages") or []
    last = messages[-1] if messages else None
    tool_calls = getattr(last, "tool_calls", None) or []
    if not tool_calls:
        return {}

    thread_id = state.get("thread_id", "")
    tool_messages = []
    for tc in tool_calls:
        query = (tc.get("args") or {}).get("query", "")
        try:
            content = await search_chat_history(query, rag_service, thread_id)
        except Exception:
            logger.exception(
                "Tool search_chat_history failed for session %s",
                state.get("session_id", ""),
            )
            content = "检索历史消息失败。"
        tool_messages.append(ToolMessage(content=content, tool_call_id=tc["id"]))
    return {"messages": tool_messages}
