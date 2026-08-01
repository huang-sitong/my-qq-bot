"""index_turn — persist the current turn into the RAG store.

Runs after ``summarize``. It is reached by both replied turns (user +
bot reply, 2 records) and non-replied group text (user only, 1 record —
``bot_reply`` is empty and ``RagService.index_turn`` filters it out).
Media-only content (empty ``clean_text``) is skipped.
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
    content = clean_text(state.get("raw_content", ""))
    if not content.strip():
        return {}  # media-only message — nothing meaningful to index
    await rag_service.index_turn(
        thread_id=state.get("thread_id", ""),
        user_id=state.get("user_id", ""),
        user_name=state.get("user_name", ""),
        user_message=content,
        bot_reply=state.get("reply_text", ""),  # empty → service indexes user only
    )
    return {}
