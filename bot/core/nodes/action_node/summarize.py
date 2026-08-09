"""summarize — compress older conversation messages into a progressive summary.

Runs after ``call_llm_node`` on every invocation. Checks whether the total
context exceeds the configured trigger threshold; if so, trims old messages
and generates a merged summary via LLM.
"""

import logging

from langchain_core.messages import (
    HumanMessage,
    RemoveMessage,
    trim_messages,
)
from langchain_core.messages.utils import count_tokens_approximately
from langchain_openai import ChatOpenAI

from bot.core.utils import content_to_text, estimate_context_tokens, format_messages_for_summary
from common import SUMMARY_PROMPT, BotConfig
from object.bot.state import BotState

logger = logging.getLogger(__name__)


def _approx_token_counter(messages) -> int:
    """trim_messages 的 approximate token 计数器（中文 1.5 字符/token）。

    langgraph/langchain 新版 trim_messages 不再接受 ``chars_per_token`` 关键字，
    改为传 callable；这里与 ``estimate_context_tokens`` 保持一致避免估算偏离。
    """
    return count_tokens_approximately(messages, chars_per_token=1.5)


async def summarize_node(
    state: BotState,
    llm: ChatOpenAI,
    bot_config: BotConfig,
) -> dict:
    """Check context size; if over threshold, summarize old messages.

    Returns ``{}`` (no-op) when below threshold or when there are no
    messages to compress.  Otherwise returns ``RemoveMessage`` updates
    and a new ``conversation_summary``.
    """
    trigger = int(bot_config.summary_trigger_ratio * bot_config.llm_context_window)

    # 1. Check if summarization is needed
    total = estimate_context_tokens(
        state["messages"],
        state.get("persona", ""),
        state.get("conversation_summary", ""),
    )
    logger.debug(
        "summarize check: total=%d trigger=%d thread=%s",
        total, trigger, state.get("thread_id", ""),
    )

    if total <= trigger:
        return {}  # No summarization needed

    # 2. Split messages: keep recent, summarize the rest
    keep_tokens = int(bot_config.summary_keep_ratio * bot_config.llm_context_window)
    keep_messages = trim_messages(
        state["messages"],
        max_tokens=keep_tokens,
        token_counter=_approx_token_counter,
        strategy="last",
        start_on="human",
    )
    keep_ids = {m.id for m in keep_messages if getattr(m, "id", None)}
    to_summarize = [m for m in state["messages"] if m.id not in keep_ids]

    if not to_summarize:
        return {}

    logger.info(
        "Summarizing %d messages (keeping %d) for thread %s",
        len(to_summarize), len(keep_messages), state.get("thread_id", ""),
    )

    # 3. Generate summary via LLM
    old_summary = state.get("conversation_summary", "")
    formatted_messages = format_messages_for_summary(to_summarize)

    # Truncate input to summarization LLM if needed
    if bot_config.summary_max_input_tokens > 0:
        trimmed_input = trim_messages(
            [HumanMessage(content=formatted_messages)],
            max_tokens=bot_config.summary_max_input_tokens,
            token_counter=_approx_token_counter,
            strategy="last",
        )
        formatted_messages = (
            trimmed_input[0].content if trimmed_input else formatted_messages
        )

    summary_prompt = SUMMARY_PROMPT.format(
        old_summary=old_summary or "（无）",
        messages=formatted_messages,
    )

    try:
        response = await llm.ainvoke([HumanMessage(content=summary_prompt)])
        if hasattr(response, "content"):
            new_summary = content_to_text(response.content)
        else:
            new_summary = str(response)
    except Exception:
        logger.exception("Summary generation failed for thread %s", state.get("thread_id", ""))
        return {}  # Non-critical — skip summarization on failure

    # 4. Remove summarized messages from state
    removes = [RemoveMessage(id=m.id) for m in to_summarize]

    logger.info(
        "Summary generated: %d chars, removed %d messages for thread %s",
        len(new_summary), len(to_summarize), state.get("thread_id", ""),
    )
    return {
        "messages": removes,
        "conversation_summary": new_summary,
    }
