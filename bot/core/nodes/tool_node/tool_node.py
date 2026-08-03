"""tool_node — 执行 call_llm 请求的工具调用。

读取 state 最后一条消息的 tool_calls，按工具名分发执行：
- search_chat_history   → rag_service 检索（thread_id 注入）
- remember_user_memory  → memory_store 保存（user_id 注入）
- recall_user_memory    → memory_store 检索（user_id 注入）

结果以 ToolMessage 写回 state。失败仅降级为占位文案，不中断对话。
"""

import logging

from langchain_core.messages import ToolMessage

from bot.core.tools import (
    recall_user_memory,
    remember_user_memory,
    search_chat_history,
)
from object.bot.state import BotState

logger = logging.getLogger(__name__)


async def tool_node(state: BotState, rag_service=None, memory_store=None) -> dict:
    messages = state.get("messages") or []
    last = messages[-1] if messages else None
    tool_calls = getattr(last, "tool_calls", None) or []
    if not tool_calls:
        return {}

    thread_id = state.get("thread_id", "")
    user_id = state.get("user_id", "")
    tool_messages = []
    for tc in tool_calls:
        name = tc.get("name", "")
        args = tc.get("args") or {}
        try:
            if name == "search_chat_history":
                content = await search_chat_history(args.get("query", ""), rag_service, thread_id)
            elif name == "remember_user_memory":
                content = await remember_user_memory(args["key"], args["value"], memory_store, user_id)
            elif name == "recall_user_memory":
                content = await recall_user_memory(args.get("keyword", ""), memory_store, user_id)
            else:
                content = f"未知工具：{name}"
        except Exception:
            logger.exception("Tool %s failed for thread %s", name, state.get("thread_id", ""))
            content = "工具执行失败。"
        tool_messages.append(ToolMessage(content=content, tool_call_id=tc.get("id", "")))
    return {"messages": tool_messages}
