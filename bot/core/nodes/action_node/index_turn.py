"""index_turn — persist the current turn (user + bot) into the RAG store.

Runs after ``summarize``, i.e. only for messages the bot actually
responded to. Replaces the old handler-side ``_index_turn``: clean the
user message, skip media-only turns, and delegate to ``rag_service``
(which swallows failures internally — indexing never blocks the reply).
"""

import logging

from bot.core.rag.service import RagService
from bot.core.utils import clean_text
from object.bot.state import BotState

logger = logging.getLogger(__name__)


async def index_turn_node(state: BotState, rag_service: RagService | None) -> dict:
    """Index the current turn into the vector store. No-op when RAG is disabled."""
    if rag_service is None:
        return {}
    reply_text = state.get("reply_text", "")
    if not reply_text:
        return {}
    content = clean_text(state.get("raw_content", ""))
    if not content.strip():
        return {}  # media-only message — nothing meaningful to index
    await rag_service.index_turn(
        thread_id=state.get("thread_id", ""),
        user_id=state.get("user_id", ""),
        user_name=state.get("user_name", ""),
        user_message=content,
        bot_reply=reply_text,
    )
    return {}
