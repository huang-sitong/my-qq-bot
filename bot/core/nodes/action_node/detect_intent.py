"""detect_intent — fast-path routing: determine if the bot should respond.

Sets ``should_respond`` from channel type and @-mention detection,
then strips the @-mention prefix and builds the HumanMessage with
correct user attribution for group chats.
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
    """Fast-path routing: check DIRECT / @-mention, build HumanMessage.

    Called BEFORE the LLM router so that explicit triggers (private
    chat, @-mention) skip the LLM routing step entirely.
    """
    channel_type = state.get("channel_type", 0)
    bot_id = state.get("bot_id", "")
    raw_content = state.get("raw_content", "")
    user_name = state.get("user_name", "")

    # 1) Decide should_respond from explicit signals
    if channel_type == ChannelType.DIRECT:
        should_respond = True
    elif bot_id and f'<at id="{bot_id}"' in raw_content:
        should_respond = True
    else:
        should_respond = False  # let router_node (LLM) decide

    # 2) Strip @-mention and build HumanMessage
    content = _strip_mention(raw_content)
    is_group = channel_type != ChannelType.DIRECT
    if is_group and user_name:
        new_message = HumanMessage(content=content, name=user_name)
    else:
        new_message = HumanMessage(content=content)

    logger.debug(
        "detect_intent: should_respond=%s channel_type=%s is_group=%s",
        should_respond, channel_type, is_group,
    )
    return {"should_respond": should_respond, "new_message": new_message}
