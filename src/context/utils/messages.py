"""消息元数据读取：发言者信息随 HumanMessage 传递。"""

from __future__ import annotations

import logging

from langchain_core.messages import BaseMessage, HumanMessage


def speaker_from_messages(
    messages: list[BaseMessage] | None,
) -> tuple[str, str]:
    """从最近一条 HumanMessage 读取 ``(user_id, user_name)``。"""
    for message in reversed(messages or []):
        if isinstance(message, HumanMessage):
            kwargs = message.additional_kwargs or {}
            user_id = str(kwargs.get("user_id", ""))
            user_name = message.name or str(kwargs.get("user_name", "")) or ""
            return user_id, user_name
    return "", ""


def format_message_for_log(message: BaseMessage) -> str:
    """把 LangChain BaseMessage 转成适合写入日志的单行文本。

    System/Human/AI/Tool 等类型都会渲染成 ``[Role]`` 或 ``[Role|name]``。
    多模态 content 只保留 text 块，图片显示为 ``[图片]``，避免把 base64/URL 大段
    写进日志。若消息带 ``tool_calls``，会在正文前追加工具调用摘要。
    """
    from .context import content_to_text

    role = type(message).__name__.replace("Message", "")
    name = getattr(message, "name", "") or ""
    head = f"[{role}"
    if name:
        head += f"|{name}"
    head += "]"

    content = content_to_text(getattr(message, "content", ""))
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        calls = []
        for call in tool_calls:
            if isinstance(call, dict):
                call_name = call.get("name", "")
                args = call.get("args", {})
                calls.append(f"{call_name}({args})")
        calls_text = ", ".join(calls)
        if calls_text:
            return f"{head} tool_calls: {calls_text} | content: {content}"
    return f"{head}: {content}"


def log_context_message(
    message: BaseMessage,
    *,
    logger,
    level: int = logging.INFO,
    prefix: str = "Context message",
    **extra: object,
) -> None:
    """按统一格式打印一条上下文消息。

    额外字段（如 ``thread_id`` / ``trace_id``）会拼到消息前，便于在日志中过滤。
    """
    extra_text = " ".join(f"{key}={value}" for key, value in extra.items())
    detail = format_message_for_log(message)
    if extra_text:
        logger.log(level, "%s %s: %s", prefix, extra_text, detail)
    else:
        logger.log(level, "%s: %s", prefix, detail)
