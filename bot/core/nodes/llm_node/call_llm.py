import logging

from langchain_core.messages import AIMessage, SystemMessage
from langchain_openai import ChatOpenAI

from bot.core.tools import (
    TOOL_SCHEMA,
    TOOL_SCHEMA_RECALL,
    TOOL_SCHEMA_REMEMBER,
)
from bot.core.utils import build_system_messages
from common import BotConfig, MEMORY_TOOL_HINT
from object.bot.state import BotState

logger = logging.getLogger(__name__)


async def call_llm_node(
    state: BotState,
    llm: ChatOpenAI,
    rag_service=None,
    memory_store=None,
    bot_config: BotConfig | None = None,
) -> dict:
    """调用 LLM 生成回复。

    SystemMessages 每次调用动态构建、不持久化，人设始终位于 messages[0]。
    注入 rag_service / memory_store 时绑定对应工具：若 LLM 请求调用工具，
    返回原始 AIMessage（带 tool_calls），由 tool_node 执行并回环重入本节点；
    否则返回最终回复。轮次达到 rag_max_agent_rounds 上限后走无工具路径收尾。
    """
    persona = state["persona"].format(bot_name=state.get("bot_name", ""))
    summary = state.get("conversation_summary", "").strip()
    system_msgs = build_system_messages(persona, summary)

    use_rag = rag_service is not None and rag_service.enabled
    use_memory = memory_store is not None
    if use_memory:
        system_msgs.append(SystemMessage(content=MEMORY_TOOL_HINT))

    messages = system_msgs + state["messages"]

    schemas = []
    if use_rag:
        schemas.append(TOOL_SCHEMA)
    if use_memory:
        schemas += [TOOL_SCHEMA_REMEMBER, TOOL_SCHEMA_RECALL]
    max_rounds = bot_config.rag_max_agent_rounds if bot_config is not None else 3
    rounds = state.get("tool_rounds", 0)

    if (use_rag or use_memory) and rounds < max_rounds:
        try:
            response = await llm.bind_tools(schemas).ainvoke(messages)
        except Exception as exc:
            _log_llm_error(exc, state.get("session_id", ""))
            return {
                "messages": [AIMessage(content="我暂时无法思考，请稍后再试")],
                "reply_text": "我暂时无法思考，请稍后再试",
            }
        if response.tool_calls:
            return {
                "messages": [response],
                "tool_rounds": rounds + 1,
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
