"""search_chat_history 工具。

纯函数：按查询检索群聊历史并格式化为上下文文本块。
rag_service 与 thread_id 由 tool_node 在调用时注入，LLM 无需知道内部标识。
"""

import logging

from bot.core.rag.service import RagService, normalize_time

logger = logging.getLogger(__name__)

TOOL_NAME = "search_chat_history"

TOOL_DESCRIPTION = (
    "检索群聊历史消息。双模式："
    "（1）语义检索——当用户询问之前讨论过的话题、事实、决定、约定时用 query 检索最相关内容；"
    "（2）按人/按内容/按时间属性检索——当用户问『某人说过什么』『谁说过xx』『bot 回复过谁』"
    "或『某时间段内』时，用 user_name / content_keyword / start_time / end_time"
    "精确过滤（更快更准）。"
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
                "start_time": {
                    "type": "string",
                    "description": "可选：时间窗口起始，ISO 格式 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS",
                },
                "end_time": {
                    "type": "string",
                    "description": "可选：时间窗口结束，ISO 格式 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS",
                },
            },
            "required": ["query"],
        },
    },
}


def _format_time(ts: str) -> str:
    """ISO 时间戳展示为 YYYY-MM-DD HH:MM（截掉秒，与旧版一致）。"""
    return ts[:16]


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
    content_keyword: str = "",
    start_time: str = "",
    end_time: str = "",
) -> str:
    """检索并格式化群聊历史，返回适合作为 ToolMessage 的文本。

    指定了 user_name 或 content_keyword 时走 SQL 属性检索（无 embedding）；
    否则走向量语义检索。start_time/end_time 是 ISO 风格字符串，入库前经
    normalize_time 规范为固定格式（非法输入返回错误提示，不抛异常）。
    """
    start, end = "", ""
    if start_time.strip() or end_time.strip():
        try:
            start = normalize_time(start_time) if start_time.strip() else ""
            end = normalize_time(end_time) if end_time.strip() else ""
        except ValueError:
            return "时间参数格式无效：请使用 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS。"

    if user_name.strip() or content_keyword.strip():
        results = await rag_service.search_by_user(
            thread_id,
            person=user_name.strip(),
            content_keyword=content_keyword.strip(),
            start_time=start,
            end_time=end,
        )
    else:
        results = await rag_service.search(
            query, thread_id, start_time=start, end_time=end)
    return _format_results(results)
