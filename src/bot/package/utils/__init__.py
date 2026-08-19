"""纯工具与横切设施包。

原 ``context.utils`` 的消息/上下文工具和原 ``common`` 的日志、队列、重试、
路径工具统一收敛到这里。保持纯函数优先，禁止反向依赖 pipeline/platform/tools。
"""

from bot.package.conversation.content import Attachment, MessageKind, ParsedContent

from .content_parser import (
    IMAGE_PLACEHOLDER,
    classify_content,
    clean_text,
    parse_attachments,
    parse_content,
    parse_mentions,
    to_llm_text,
)
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
from .reply_policy import should_allow_auto_reply
from .routing import decide_reply, is_explicit_request, keep_in_context

__all__ = [
    "IMAGE_PLACEHOLDER",
    "Attachment",
    "MessageKind",
    "ParsedContent",
    "build_system_messages",
    "classify_content",
    "clean_text",
    "content_to_text",
    "decide_reply",
    "estimate_context_tokens",
    "format_message_for_log",
    "format_messages_for_summary",
    "is_explicit_request",
    "keep_in_context",
    "log_context_message",
    "parse_attachments",
    "parse_content",
    "parse_mentions",
    "should_allow_auto_reply",
    "speaker_from_messages",
    "to_llm_text",
]
