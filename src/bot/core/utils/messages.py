"""消息元数据读取：发言者信息随 HumanMessage 传递。"""

from __future__ import annotations

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
