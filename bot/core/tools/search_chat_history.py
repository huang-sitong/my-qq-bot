"""search_chat_history 工具。

纯函数：按查询检索群聊历史并格式化为上下文文本块。
rag_service 与 thread_id 由 rag_tool_node 在调用时注入，LLM 无需知道内部标识。
"""

import logging
import time

from bot.core.rag.service import RagService

logger = logging.getLogger(__name__)

TOOL_NAME = "search_chat_history"

TOOL_DESCRIPTION = (
    "检索群聊历史消息中与给定问题最相关的记录。"
    "当用户询问之前讨论过的话题、事实、决定、约定或个人偏好时使用，"
    "以获取准确的历史上下文进行回复。"
)

TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": TOOL_DESCRIPTION,
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "要检索的问题或关键词，用中文表述",
                },
            },
            "required": ["query"],
        },
    },
}


def _format_time(ts: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def _format_results(results: list[dict]) -> str:
    if not results:
        return "没有找到相关的历史消息。"
    lines = []
    for r in results:
        speaker = r["user_name"] or ("我" if r["role"] == "assistant" else r["role"])
        lines.append(f"[{_format_time(r['timestamp'])}] {speaker}: {r['content']}")
    return "\n".join(lines)


async def search_chat_history(query: str, rag_service: RagService, thread_id: str) -> str:
    """检索并格式化群聊历史，返回适合作为 ToolMessage 的文本。"""
    results = await rag_service.search(query, thread_id)
    return _format_results(results)
