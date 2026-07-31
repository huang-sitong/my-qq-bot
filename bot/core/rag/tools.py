"""search_chat_history 工具工厂。

闭包捕获当前 thread_id，LLM 无需知道内部标识。检索结果格式化为
带发言人、时间戳、角色的上下文文本块。
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


def make_search_tool(
    rag: RagService,
    thread_id: str,
    top_k: int | None = None,
    score_threshold: float | None = None,
) -> tuple[dict, object]:
    """生成 (工具定义, 异步执行函数) 对，供 call_llm_node 的 ReAct 循环使用。"""

    async def search_chat_history(query: str) -> str:
        results = await rag.search(query, thread_id, top_k, score_threshold)
        return _format_results(results)

    search_chat_history.__name__ = TOOL_NAME
    return TOOL_SCHEMA, search_chat_history
