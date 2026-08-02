"""Context window utilities for token estimation and message formatting.

Wraps LangChain built-in ``count_tokens_approximately`` and
``trim_messages`` for the QQ bot's three-layer context structure.
"""

import logging

from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.messages.utils import count_tokens_approximately

logger = logging.getLogger(__name__)

# Chinese text averages ~1.5 characters per token (vs ~4 for English)
_CHARS_PER_TOKEN = 1.5


def build_system_messages(persona: str, summary: str = "") -> list[SystemMessage]:
    """构建 call_llm 的前两层 SystemMessage；estimate_context_tokens 复用保证估算一致。

    与 ``call_llm_node`` 注入的层级结构完全相同——token 估算与实际上下文永不偏离。
    """
    if not persona.strip():
        return []
    msgs = [SystemMessage(content=persona)]
    if summary.strip():
        msgs.append(SystemMessage(content=f"之前的对话摘要：\n{summary}"))
    return msgs


def estimate_context_tokens(
    messages: list[BaseMessage],
    persona: str,
    summary: str,
) -> int:
    """Estimate total tokens for the full context sent to the LLM.

    Builds the same layer structure that ``call_llm_node`` uses
    and passes it through ``count_tokens_approximately`` for a single
    consistent token count.
    """
    # Layer 0 + 1: persona + conversation summary（构造与 call_llm 共用 build_system_messages）
    all_msgs = build_system_messages(persona, summary)

    # Layer 2..N: recent messages
    all_msgs.extend(messages)

    return count_tokens_approximately(
        all_msgs,
        chars_per_token=_CHARS_PER_TOKEN,
    )


def format_messages_for_summary(messages: list[BaseMessage]) -> str:
    """Convert a list of messages to a readable text block for summarization.

    Each message is formatted as ``[Role | name]: content`` or
    ``[Role]: content``, one per line.
    """
    lines: list[str] = []
    for m in messages:
        role = type(m).__name__.replace("Message", "")
        content = getattr(m, "content", str(m))
        name = getattr(m, "name", "") or ""
        if name:
            lines.append(f"[{role} | {name}]: {content}")
        else:
            lines.append(f"[{role}]: {content}")
    return "\n".join(lines)
