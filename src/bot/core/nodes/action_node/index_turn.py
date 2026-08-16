"""index_turn — persist the current turn into the RAG store.

Runs after ``summarize``. It is reached by both replied turns (user +
bot reply, 2 records) and non-replied group text (user only, 1 record —
``bot_reply`` is empty and ``RagService.index_turn`` filters it out).
``clean_text`` 由 handler 预计算注入（``parse_content`` 产出），本节点直接
消费、不再图内解析 raw_content。image 轮统一追加 ``IMAGE_PLACEHOLDER``；
纯媒体但**有回复**时（多模态 vision 关闭的图片轮），仍把承载主 LLM
理解的 ``reply_text`` 作为 assistant 记录入库，不整轮跳过。
"""

import logging

from bot.core.utils import IMAGE_PLACEHOLDER, MessageKind, content_to_text, speaker_from_messages
from conversation.state import BotState
from knowledge.service import RagService

logger = logging.getLogger(__name__)


async def index_turn_node(state: BotState, rag_service: RagService | None) -> dict:
    """Index the current turn into the vector store. No-op when RAG is disabled."""
    if rag_service is None:
        return {}
    content = state.get("clean_text", "")
    user_id, user_name = speaker_from_messages(state.get("messages"))
    # reply_text 可能是旧 checkpoint 残留的多模态 content 块列表 → 先归一化再 strip
    reply_text = content_to_text(state.get("reply_text", "")).strip()
    if (
        state.get("content_kind") == MessageKind.IMAGE.value
        and (content.strip() or reply_text)
    ):
        content = f"{content} {IMAGE_PLACEHOLDER}".strip()
    # 纯媒体且无描述、也无回复 → 无可索引内容。
    # 否则即使 user 侧为空（如多模态 vision 关闭的图片轮），reply_text 仍承载
    # 主 LLM 对图的理解，作为 assistant 记录入库（RagService.index_turn 过滤空对）。
    if not content.strip() and not reply_text:
        return {}
    await rag_service.index_turn(
        thread_id=state.get("thread_id", ""),
        user_id=user_id,
        user_name=user_name,
        bot_id=state.get("bot_id", ""),
        bot_name=state.get("bot_name", ""),
        user_message=content,
        bot_reply=reply_text,  # empty → service indexes user only
    )
    return {}
