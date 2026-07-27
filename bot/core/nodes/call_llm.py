import logging

from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI

from object.bot.state import BotState

logger = logging.getLogger(__name__)


async def call_llm_node(state: BotState, llm: ChatOpenAI) -> dict:
    """Call the LLM and extract reply text."""
    try:
        response = await llm.ainvoke(state["messages"])
        reply = response.content if hasattr(response, "content") else str(response)
    except Exception as exc:
        if isinstance(exc, type(TimeoutError(""))) or "Timeout" in type(exc).__name__:
            logger.warning("LLM call timed out for session %s", state["session_id"])
        else:
            logger.exception("LLM call failed for session %s", state["session_id"])
        reply = "我暂时无法思考，请稍后再试"

    return {"messages": [AIMessage(content=reply)], "reply_text": reply}
