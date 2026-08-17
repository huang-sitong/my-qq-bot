"""上下文管理上下文。

提供上下文构建/估算、消息解析、回复判定等纯函数（``context.utils``）。
``ContextCompactor`` 通过 ``__getattr__`` 延迟加载，避免与 ``orchestration``
形成导入环。
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
    "ContextCompactor",
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


def __getattr__(name: str):
    if name == "ContextCompactor":
        from context.compaction import ContextCompactor

        return ContextCompactor
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
