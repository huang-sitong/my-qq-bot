import logging

from langchain_core.messages import AIMessage, SystemMessage
from langchain_openai import ChatOpenAI

from object.bot.state import BotState

logger = logging.getLogger(__name__)


async def call_llm_node(state: BotState, llm: ChatOpenAI) -> dict:
    """Call the LLM with dynamically injected persona and extract reply text.

    The SystemMessage is built fresh each invocation and never persisted
    to checkpoint, so the persona is always at messages[0] regardless of
    conversation length.
    """
    # Build dynamic SystemMessage
    system_content = state["persona"]
    memories = state.get("user_memories", "").strip()
    if memories:
        system_content += f"\n\n关于当前用户已知的信息：\n{memories}"

    # Prepend SystemMessage for this call only (not persisted)
    messages = [SystemMessage(content=system_content)] + state["messages"]

    try:
        response = await llm.ainvoke(messages)
        reply = response.content if hasattr(response, "content") else str(response)
    except Exception as exc:
        if isinstance(exc, type(TimeoutError(""))) or "Timeout" in type(exc).__name__:
            logger.warning("LLM call timed out for session %s", state["session_id"])
        else:
            logger.exception("LLM call failed for session %s", state["session_id"])
        reply = "我暂时无法思考，请稍后再试"

    return {"messages": [AIMessage(content=reply)], "reply_text": reply}
