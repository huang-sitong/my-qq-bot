"""search_chat_history 工具（纯函数）。

纯函数：按查询检索群聊历史并格式化为上下文文本块。
rag_service 与 thread_id 由 factory 包装层在调用时注入，LLM 无需知道内部标识。
"""

import logging

from bot.core.rag.service import RagService, normalize_time

logger = logging.getLogger(__name__)


def _format_time(ts: str) -> str:
    """ISO 时间戳展示为 YYYY-MM-DD HH:MM（截掉秒，与旧版一致）。"""
    return ts[:16]


def _thread_label(thread_id: str) -> str:
    """thread_id = platform:guild:channel → 来源群短标签（guild 段）。

    属性检索跨群后，跨群结果靠它标注来源，避免同昵称不同群混淆。
    """
    parts = thread_id.split(":")
    return parts[1] if len(parts) >= 2 else thread_id


def _format_results(results: list[dict]) -> str:
    if not results:
        return "没有找到相关的历史消息。"
    # 结果跨多个群时才标来源群；单群保持旧格式（无群标签，不产生噪音）
    multi = len({r.get("thread_id") for r in results}) > 1
    lines = []
    for r in results:
        speaker = r.get("sender_name") or "?"
        receiver = r.get("receiver_name") or ""
        prefix = f"{speaker} → {receiver}" if receiver else speaker
        src = f"[{_thread_label(r['thread_id'])}] " if multi else ""
        lines.append(f"[{_format_time(r['timestamp'])}] {src}{prefix}: {r['content']}")
    return "\n".join(lines)


async def search_chat_history(
    query: str,
    rag_service: RagService,
    thread_id: str,
    user_name: str = "",
    hours: int = 0,
    content_keyword: str = "",
    start_time: str = "",
    end_time: str = "",
) -> str:
    """检索并格式化群聊历史，返回适合作为 ToolMessage 的文本。

    指定了 user_name 或 content_keyword 时走属性检索（sparse BM25 信号，无 dense embedding），
    **跨全部群**（thread_id 置 None，结果标注来源群）；否则走向量语义检索
    （当前群优先，不足时跨群补齐）。start_time/end_time 是 ISO 风格字符串，
    入库前经 normalize_time 规范为固定格式（非法输入返回错误提示，不抛异常）。
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
            None,  # 属性检索跨全部群，取消群聊限制
            person=user_name.strip(),
            content_keyword=content_keyword.strip(),
            hours=hours or 0,
            start_time=start,
            end_time=end,
        )
    else:
        results = await rag_service.search(
            query, thread_id, hours=hours or 0, start_time=start, end_time=end)
    return _format_results(results)
