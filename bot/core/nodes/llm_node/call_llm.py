import logging

from langchain_core.messages import AIMessage, SystemMessage
from langchain_openai import ChatOpenAI

from object.bot.state import BotState

logger = logging.getLogger(__name__)


async def call_llm_node(state: BotState, llm: ChatOpenAI) -> dict:
    """Call the LLM with dynamically injected persona, memories, and summary.

    SystemMessages are built fresh each invocation and never persisted
    to checkpoint, so the persona is always at messages[0] regardless of
    conversation length.
    """
    # Build dynamic SystemMessages (never persisted to checkpoint)
    persona = state["persona"].format(bot_name=state.get("bot_name", ""))
    system_msgs = [SystemMessage(content=persona)]

    # Layer 1: conversation summary (optional)
    summary = state.get("conversation_summary", "").strip()
    if summary:
        system_msgs.append(SystemMessage(
            content=f"之前的对话摘要：\n{summary}"
        ))

    # Layer 2: user memories (optional)
    memories = state.get("user_memories", "").strip()
    if memories:
        system_msgs.append(SystemMessage(
            content=f"关于当前用户已知的信息：\n{memories}"
        ))

    # Layer 3..N: recent messages
    messages = system_msgs + state["messages"]

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
