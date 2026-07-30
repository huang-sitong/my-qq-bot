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


def estimate_context_tokens(
    messages: list[BaseMessage],
    persona: str,
    memories: str,
    summary: str,
) -> int:
    """Estimate total tokens for the full context sent to the LLM.

    Builds the same three-layer structure that ``call_llm_node`` uses
    and passes it through ``count_tokens_approximately`` for a single
    consistent token count.
    """
    all_msgs: list[BaseMessage] = []

    # Layer 0: persona (always present)
    if persona.strip():
        all_msgs.append(SystemMessage(content=persona))

    # Layer 1: conversation summary (optional)
    if summary.strip():
        all_msgs.append(SystemMessage(
            content=f"之前的对话摘要：\n{summary}"
        ))

    # Layer 2: user memories (optional)
    if memories.strip():
        all_msgs.append(SystemMessage(
            content=f"关于当前用户已知的信息：\n{memories}"
        ))

    # Layer 3..N: recent messages
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
