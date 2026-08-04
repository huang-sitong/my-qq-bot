"""search_chat_history 工具。

纯函数：按查询检索群聊历史并格式化为上下文文本块。
rag_service 与 thread_id 由 tool_node 在调用时注入，LLM 无需知道内部标识。
"""

import logging
import time

from bot.core.rag.service import RagService

logger = logging.getLogger(__name__)

TOOL_NAME = "search_chat_history"

TOOL_DESCRIPTION = (
    "检索群聊历史消息。双模式："
    "（1）语义检索——当用户询问之前讨论过的话题、事实、决定、约定时用 query 检索最相关内容；"
    "（2）按人/按内容属性检索——当用户问『某人说过什么』『谁说过xx』『bot 回复过谁』时，"
    "用 user_name / content_keyword 精确过滤（更快更准）。"
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
                    "description": "要检索的问题或关键词，用中文表述（语义检索模式；user_name/content_keyword 非空时忽略）",
                },
                "user_name": {
                    "type": "string",
                    "description": "可选：指定涉及的用户昵称，模糊匹配 TA 的发言或 bot 给 TA 的回复",
                },
                "content_keyword": {
                    "type": "string",
                    "description": "可选：按内容包含的关键词过滤，用于查『谁说过 xx』",
                },
                "hours": {
                    "type": "integer",
                    "description": "可选：只看最近 N 小时内的消息，默认不限",
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
        speaker = r.get("sender_name") or "?"
        receiver = r.get("receiver_name") or ""
        prefix = f"{speaker} → {receiver}" if receiver else speaker
        lines.append(f"[{_format_time(r['timestamp'])}] {prefix}: {r['content']}")
    return "\n".join(lines)


async def search_chat_history(
    query: str,
    rag_service: RagService,
    thread_id: str,
    user_name: str = "",
    hours: int = 0,
    content_keyword: str = "",
) -> str:
    """检索并格式化群聊历史，返回适合作为 ToolMessage 的文本。

    指定了 user_name 或 content_keyword 时走 SQL 属性检索（无 embedding）；
    否则走向量语义检索。
    """
    if user_name.strip() or content_keyword.strip():
        results = await rag_service.search_by_user(
            thread_id,
            person=user_name.strip(),
            content_keyword=content_keyword.strip(),
            hours=hours or 0,
        )
    else:
        results = await rag_service.search(query, thread_id)
    return _format_results(results)
