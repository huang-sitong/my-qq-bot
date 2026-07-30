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
from langchain_openai import ChatOpenAI

from bot.core.context import estimate_context_tokens, format_messages_for_summary
from common import BotConfig, SUMMARY_PROMPT
from object.bot.state import BotState

logger = logging.getLogger(__name__)


async def summarize_node(
    state: BotState,
    llm: ChatOpenAI,
    config: BotConfig,
) -> dict:
    """Check context size; if over threshold, summarize old messages.

    Returns ``{}`` (no-op) when below threshold or when there are no
    messages to compress.  Otherwise returns ``RemoveMessage`` updates
    and a new ``conversation_summary``.
    """
    trigger = int(config.summary_trigger_ratio * config.llm_context_window)

    # 1. Check if summarization is needed
    total = estimate_context_tokens(
        state["messages"],
        state.get("persona", ""),
        state.get("user_memories", ""),
        state.get("conversation_summary", ""),
    )
    logger.debug(
        "summarize check: total=%d trigger=%d session=%s",
        total, trigger, state.get("session_id", ""),
    )

    if total <= trigger:
        return {}  # No summarization needed

    # 2. Split messages: keep recent, summarize the rest
    keep_tokens = int(config.summary_keep_ratio * config.llm_context_window)
    keep_messages = trim_messages(
        state["messages"],
        max_tokens=keep_tokens,
        token_counter="approximate",
        strategy="last",
        start_on="human",
        chars_per_token=1.5,
    )
    keep_ids = {m.id for m in keep_messages if getattr(m, "id", None)}
    to_summarize = [m for m in state["messages"] if m.id not in keep_ids]

    if not to_summarize:
        return {}

    logger.info(
        "Summarizing %d messages (keeping %d) for session %s",
        len(to_summarize), len(keep_messages), state.get("session_id", ""),
    )

    # 3. Generate summary via LLM
    old_summary = state.get("conversation_summary", "")
    formatted_messages = format_messages_for_summary(to_summarize)

    # Truncate input to summarization LLM if needed
    if config.summary_max_input_tokens > 0:
        trimmed_input = trim_messages(
            [HumanMessage(content=formatted_messages)],
            max_tokens=config.summary_max_input_tokens,
            token_counter="approximate",
            strategy="last",
            chars_per_token=1.5,
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
        new_summary = response.content if hasattr(response, "content") else str(response)
    except Exception:
        logger.exception("Summary generation failed for session %s", state.get("session_id"))
        return {}  # Non-critical — skip summarization on failure

    # 4. Remove summarized messages from state
    removes = [RemoveMessage(id=m.id) for m in to_summarize]

    logger.info(
        "Summary generated: %d chars, removed %d messages for session %s",
        len(new_summary), len(to_summarize), state.get("session_id"),
    )
    return {
        "messages": removes,
        "conversation_summary": new_summary,
    }
