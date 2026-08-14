"""detect_intent — deterministic routing: decide if the bot should respond.

``should_respond`` is decided purely from channel type, content kind and
@-mention detection (no LLM router):
- text/image: reply on private chat or group top-level @-mention (id + name, see parse_mentions)
- file/audio/video: never reply (even in private chat)

Media messages that are NOT replied to are kept out of ``messages`` so their
placeholders never pollute later context. HumanMessage 内容用 handler 注入的
llm_text（每轮必注入，无兜底）。
"""

import logging

from langchain_core.messages import HumanMessage

from bot.core.utils.routing import decide_reply, keep_in_context
from domain.bot.state import BotState

logger = logging.getLogger(__name__)


async def detect_intent(state: BotState, user_name: str = "") -> dict:
    """Deterministic routing: decide should_respond, build HumanMessage.

    The old LLM router is gone: group messages only get a reply on
    @-mention. Non-replied text still enters context (later summarized +
    single-record indexed); non-replied media is dropped entirely.
    """
    channel_type = state.get("channel_type", 0)
    bot_id = state.get("bot_id", "")
    bot_name = state.get("bot_name", "")
    mentions = state.get("mentions", {})
    content_kind = state.get("content_kind", "")
    auto_reply = state.get("auto_reply", False)
    has_text = state.get("has_text", False)

    # 判定表（decide_reply / keep_in_context）单一来源见 bot.core.utils.routing
    should_respond = decide_reply(channel_type, content_kind, bot_id, bot_name, mentions, auto_reply)

    # 2) Build HumanMessage: handler 每轮必注入 llm_text（媒体->占位符、@ 已渲染）
    #    发言者元数据随消息携带，不再作为图状态标量字段。
    content = state.get("llm_text", "")
    kwargs = {
        "user_id": state.get("user_id", ""),
        "user_name": user_name,
        "image_srcs": state.get("image_srcs", []),
    }
    message = HumanMessage(
        content=content,
        name=user_name or None,
        additional_kwargs={k: v for k, v in kwargs.items() if v},
    )

    # 3) Non-replied media must NOT enter context — its placeholder would
    #    pollute later @-mention turns. Keep them out of ``messages``.
    add_to_context = keep_in_context(should_respond, content_kind, has_text)
    logger.debug(
        "detect_intent: should_respond=%s channel_type=%s content_kind=%s has_text=%s add_to_context=%s",
        should_respond, channel_type, content_kind, has_text, add_to_context,
    )
    return {
        "should_respond": should_respond,
        "messages": [message] if add_to_context else [],
    }
