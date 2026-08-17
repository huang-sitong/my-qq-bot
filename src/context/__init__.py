"""上下文管理上下文。

提供上下文构建/估算、消息解析、回复判定等纯函数（``context.utils``）。
上下文压缩服务 ``ContextCompactor`` 属于编排层，已移至 ``orchestration``。
"""

from .utils import (
    IMAGE_PLACEHOLDER,
    Attachment,
    MessageKind,
    ParsedContent,
    build_system_messages,
    classify_content,
    clean_text,
    content_to_text,
    estimate_context_tokens,
    format_messages_for_summary,
    parse_attachments,
    parse_content,
    parse_mentions,
    speaker_from_messages,
    to_llm_text,
)

__all__ = [
    "IMAGE_PLACEHOLDER",
    "Attachment",
    "MessageKind",
    "ParsedContent",
    "build_system_messages",
    "classify_content",
    "clean_text",
    "content_to_text",
    "estimate_context_tokens",
    "format_messages_for_summary",
    "parse_attachments",
    "parse_content",
    "parse_mentions",
    "speaker_from_messages",
    "to_llm_text",
]
