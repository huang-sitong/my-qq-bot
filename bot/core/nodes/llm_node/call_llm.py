import logging

from langchain_core.messages import AIMessage, SystemMessage
from langchain_openai import ChatOpenAI

from bot.core.tools import TOOL_SCHEMA
from common import BotConfig
from object.bot.state import BotState

logger = logging.getLogger(__name__)


async def call_llm_node(
    state: BotState,
    llm: ChatOpenAI,
    rag_service=None,
    bot_config: BotConfig | None = None,
) -> dict:
    """调用 LLM 生成回复。

    SystemMessages 每次调用动态构建、不持久化，人设始终位于 messages[0]。

    当注入 rag_service 且启用时，绑定 search_chat_history 工具：若 LLM
    请求调用工具，返回原始 AIMessage（带 tool_calls），由 tool_node 执行并
    回环重入本节点；否则返回最终回复。轮次达到 rag_max_agent_rounds 上限
    后走无工具路径强制收尾。
    """
    persona = state["persona"].format(bot_name=state.get("bot_name", ""))
    system_msgs = [SystemMessage(content=persona)]

    summary = state.get("conversation_summary", "").strip()
    if summary:
        system_msgs.append(SystemMessage(content=f"之前的对话摘要：\n{summary}"))

    memories = state.get("user_memories", "").strip()
    if memories:
        system_msgs.append(SystemMessage(content=f"关于当前用户已知的信息：\n{memories}"))

    messages = system_msgs + state["messages"]

    use_rag = rag_service is not None and rag_service.enabled
    max_rounds = bot_config.rag_max_agent_rounds if bot_config is not None else 3
    rounds = state.get("rag_tool_rounds", 0)

    if use_rag and rounds < max_rounds:
        try:
            response = await llm.bind_tools([TOOL_SCHEMA]).ainvoke(messages)
        except Exception as exc:
            _log_llm_error(exc, state.get("session_id", ""))
            return {
                "messages": [AIMessage(content="我暂时无法思考，请稍后再试")],
                "reply_text": "我暂时无法思考，请稍后再试",
            }
        if response.tool_calls:
            return {
                "messages": [response],
                "rag_tool_rounds": rounds + 1,
                "reply_text": "",
            }
        return {
            "messages": [AIMessage(content=response.content)],
            "reply_text": response.content,
        }

    reply = await _invoke_plain(messages, llm, state)
    return {"messages": [AIMessage(content=reply)], "reply_text": reply}


async def _invoke_plain(messages: list, llm: ChatOpenAI, state: BotState) -> str:
    """无工具路径：一次 LLM 调用。"""
    try:
        response = await llm.ainvoke(messages)
        return response.content if hasattr(response, "content") else str(response)
    except Exception as exc:
        _log_llm_error(exc, state.get("session_id", ""))
        return "我暂时无法思考，请稍后再试"


def _log_llm_error(exc: Exception, session_id: str) -> None:
    if isinstance(exc, TimeoutError) or "Timeout" in type(exc).__name__:
        logger.warning("LLM call timed out for session %s", session_id)
    else:
        logger.exception("LLM call failed for session %s", session_id)
