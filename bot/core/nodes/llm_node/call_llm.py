import logging

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI

from bot.core.rag.tools import make_search_tool
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

    当注入 rag_service 且启用时，进入 ReAct 循环：LLM 可调用
    search_chat_history 工具检索群聊历史。工具调用的中间消息只存在于
    循环局部，最终只将回复 AIMessage 写回 state/checkpoint。
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
    reply = (
        await _invoke(messages, llm, state, rag_service, bot_config)
        if use_rag
        else await _invoke_plain(messages, llm, state)
    )

    return {"messages": [AIMessage(content=reply)], "reply_text": reply}


async def _invoke_plain(messages: list, llm: ChatOpenAI, state: BotState) -> str:
    """无工具路径：一次 LLM 调用。"""
    try:
        response = await llm.ainvoke(messages)
        return response.content if hasattr(response, "content") else str(response)
    except Exception as exc:
        _log_llm_error(exc, state.get("session_id", ""))
        return "我暂时无法思考，请稍后再试"


async def _invoke(
    messages: list,
    llm: ChatOpenAI,
    state: BotState,
    rag_service,
    bot_config: BotConfig | None,
) -> str:
    """ReAct 循环：LLM ↔ search_chat_history 工具，直到 LLM 给出最终回复。"""
    thread_id = state.get("thread_id", "")
    max_rounds = bot_config.rag_max_agent_rounds if bot_config is not None else 3

    tool_def, tool_fn = make_search_tool(rag_service, thread_id)
    llm_tools = llm.bind_tools([tool_def])

    for _ in range(max_rounds):
        try:
            response = await llm_tools.ainvoke(messages)
        except Exception as exc:
            _log_llm_error(exc, state.get("session_id", ""))
            return "我暂时无法思考，请稍后再试"

        messages.append(response)  # 局部持有，不入 checkpoint
        if not response.tool_calls:
            break

        for tc in response.tool_calls:
            args = tc.get("args", {})
            try:
                result = await tool_fn(**args)
            except Exception:
                logger.exception("Tool %s failed for session %s", tc.get("name"), state.get("session_id"))
                result = "检索历史消息失败。"
            messages.append(ToolMessage(content=result, tool_call_id=tc["id"]))
    else:
        # 循环耗尽仍未给出最终回复，退化为可用内容
        logger.warning("ReAct loop exhausted for session %s", state.get("session_id"))

    if hasattr(response, "tool_calls") and response.tool_calls:
        return response.content or "我暂时无法思考，请稍后再试"
    return response.content if hasattr(response, "content") else str(response)


def _log_llm_error(exc: Exception, session_id: str) -> None:
    if isinstance(exc, TimeoutError) or "Timeout" in type(exc).__name__:
        logger.warning("LLM call timed out for session %s", session_id)
    else:
        logger.exception("LLM call failed for session %s", session_id)
