import logging

from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI

from bot.core.utils import build_system_messages, content_to_text
from common import (
    BASH_TOOL_HINT,
    FILE_SEND_TOOL_HINT,
    MCP_TOOL_HINT,
    MEMORY_TOOL_HINT,
    BotConfig,
)
from object.bot.state import BotState

logger = logging.getLogger(__name__)


async def call_llm_node(
    state: BotState,
    llm: ChatOpenAI,
    tools: list[BaseTool] | None = None,
    use_memory: bool = False,
    use_mcp: bool = False,
    use_bash: bool = False,
    use_file_send: bool = False,
    bot_config: BotConfig | None = None,
    skill_registry=None,
) -> dict:
    """调用 LLM 生成回复。

    SystemMessages 每次调用动态构建、不持久化，人设始终位于 messages[0]。
    tools 非空时绑定工具：若 LLM 请求调用工具，返回原始 AIMessage（带
    tool_calls），由 ToolNode 执行并回环重入本节点；否则返回最终回复。
    轮次达到 rag_max_agent_rounds 上限后走无工具路径收尾。
    """
    persona = state["persona"].format(bot_name=state.get("bot_name", ""))
    summary = state.get("conversation_summary", "").strip()
    system_msgs = build_system_messages(
        persona, summary,
        skill_registry=skill_registry,
        active_skills=state.get("active_skills", []),
    )
    if use_memory:
        system_msgs.append(SystemMessage(content=MEMORY_TOOL_HINT))
    if use_mcp:
        system_msgs.append(SystemMessage(content=MCP_TOOL_HINT))
    if use_bash:
        system_msgs.append(SystemMessage(content=BASH_TOOL_HINT))
    if use_file_send:
        system_msgs.append(SystemMessage(content=FILE_SEND_TOOL_HINT))

    messages = system_msgs + state["messages"]

    tools = tools or []
    max_rounds = bot_config.rag_max_agent_rounds if bot_config is not None else 3
    rounds = state.get("tool_rounds", 0)

    if tools and rounds < max_rounds:
        try:
            response = await llm.bind_tools(tools).ainvoke(messages)
        except Exception as exc:
            _log_llm_error(exc, state.get("thread_id", ""))
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
        # reply_text 必须归一化为字符串：多模态主 LLM 的 content 是块列表，
        # 直接透传给 index_turn/.strip() 或 send_message 会崩。
        return {
            "messages": [AIMessage(content=response.content)],
            "reply_text": content_to_text(response.content),
        }

    reply = await _invoke_plain(messages, llm, state)
    return {"messages": [AIMessage(content=reply)], "reply_text": reply}


async def _invoke_plain(messages: list, llm: ChatOpenAI, state: BotState) -> str:
    """无工具路径：一次 LLM 调用。"""
    try:
        response = await llm.ainvoke(messages)
        if hasattr(response, "content"):
            return content_to_text(response.content)
        return str(response)
    except Exception as exc:
        _log_llm_error(exc, state.get("thread_id", ""))
        return "我暂时无法思考，请稍后再试"


def _log_llm_error(exc: Exception, thread_id: str) -> None:
    if isinstance(exc, TimeoutError) or "Timeout" in type(exc).__name__:
        logger.warning("LLM call timed out for thread %s", thread_id)
    else:
        logger.exception("LLM call failed for thread %s", thread_id)
