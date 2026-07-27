import logging

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from bot.core.prompts import ROUTER_PROMPT
from object.bot.state import BotState

logger = logging.getLogger(__name__)


async def router_node(state: BotState, llm: ChatOpenAI) -> dict:
    """Decide whether the bot should respond.

    Fast path (should_respond already True from handler): no-op.
    Slow path: call LLM to check if the message mentions the bot by name.
    """
    if state.get("should_respond", True):
        return {}
    prompt = ROUTER_PROMPT.format(bot_name=state.get("bot_name", ""))
    try:
        response = await llm.ainvoke([
            SystemMessage(content=prompt),
            HumanMessage(content=f"消息内容：{state['new_message'].content}"),
        ])
        should_respond = "true" in response.content.strip().lower()
    except Exception:
        logger.warning("Router LLM call failed for session %s", state["session_id"])
        should_respond = False
    logger.debug("Router decision: should_respond=%s", should_respond)
    return {"should_respond": should_respond}
