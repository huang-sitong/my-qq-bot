"""detect_intent — deterministic routing: decide if the bot should respond.

``should_respond`` is decided purely from channel type, content kind and
@-mention detection (no LLM router):
- text/image: reply on private chat or group @-mention
- file/audio/video: never reply (even in private chat)

Media messages that are NOT replied to are kept out of ``messages`` so their
placeholders never pollute later context.
"""

import logging

from langchain_core.messages import HumanMessage

from object.bot.state import BotState
from object.satori import ChannelType

logger = logging.getLogger(__name__)


def _strip_mention(content: str) -> str:
    """Remove a leading ``<at …/>`` mention tag from message content."""
    idx = content.find(">")
    if idx != -1:
        return content[idx + 1:].lstrip()
    return content


async def detect_intent(state: BotState) -> dict:
    """Deterministic routing: decide should_respond, build HumanMessage.

    The old LLM router is gone: group messages only get a reply on
    @-mention. Non-replied text still enters context (later summarized +
    single-record indexed); non-replied media is dropped entirely.
    """
    channel_type = state.get("channel_type", 0)
    bot_id = state.get("bot_id", "")
    raw_content = state.get("raw_content", "")
    user_name = state.get("user_name", "")
    content_kind = state.get("content_kind", "")

    # 1) Decide should_respond — deterministic, no LLM. media never replies
    #    (the media gate is checked before DIRECT so a private file stays silent).
    if content_kind in ("file", "audio", "video"):
        should_respond = False
    elif channel_type == ChannelType.DIRECT:
        should_respond = True
    elif bot_id and f'<at id="{bot_id}"' in raw_content:
        should_respond = True
    else:
        should_respond = False  # 群聊非@：确定性不回复（不再走 LLM router）

    # 2) Build HumanMessage: prefer handler-computed llm_text (media->placeholder,
    #    @ stripped); fall back to stripping the leading mention ourselves.
    content = state.get("llm_text")
    if content is None:
        content = _strip_mention(raw_content)
    is_group = channel_type != ChannelType.DIRECT
    if is_group and user_name:
        new_message = HumanMessage(content=content, name=user_name)
    else:
        new_message = HumanMessage(content=content)

    # 3) Non-replied media must NOT enter context — its placeholder would
    #    pollute later @-mention turns. Keep them out of ``messages``.
    add_to_context = should_respond or content_kind == "text"
    logger.debug(
        "detect_intent: should_respond=%s channel_type=%s content_kind=%s add_to_context=%s",
        should_respond, channel_type, content_kind, add_to_context,
    )
    return {
        "should_respond": should_respond,
        "new_message": new_message,
        "messages": [new_message] if add_to_context else [],
    }
