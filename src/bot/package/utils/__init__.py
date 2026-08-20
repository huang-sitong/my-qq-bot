"""纯技术横切设施包。

仅包含日志、路径、队列、重试、进程内事件总线与 LangChain 消息/上下文工具。
会话业务策略与消息协议解析不在此包：策略在 ``conversation``，Satori 解析在
``platform.satori``。禁止反向依赖 pipeline/platform/tools。
"""

from .context import (
    build_system_messages,
    content_to_text,
    estimate_context_tokens,
    format_messages_for_summary,
)
from .messages import (
    format_message_for_log,
    log_context_message,
    speaker_from_messages,
)

__all__ = [
    "build_system_messages",
    "content_to_text",
    "estimate_context_tokens",
    "format_message_for_log",
    "format_messages_for_summary",
    "log_context_message",
    "speaker_from_messages",
]
