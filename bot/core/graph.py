import logging
import os
from functools import partial

import aiosqlite
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from langgraph.prebuilt import ToolNode
from langgraph.prebuilt.tool_node import ToolInvocationError

from bot.core.nodes import (
    call_llm_node,
    describe_image_node,
    detect_intent,
    index_turn_node,
    summarize_node,
)
from bot.core.tools import build_tools
from bot.core.utils.routing import route_after_detect
from common import BotConfig
from object.bot.state import BotState

logger = logging.getLogger(__name__)


def _tool_error_message(exc: Exception) -> str:
    """ToolNode 异常降级回调：只记异常类名，返回占位文案。

    只记 ``type(exc).__name__``、绝不记 repr/traceback——MCP 远程工具（如 Tavily）
    传输层异常（超时/5xx）的 repr 内嵌完整 URL（含 tavilyApiKey），泄漏即密钥泄漏。
    返回 ``工具执行失败。`` 让 LLM 继续，而不是让异常中断整轮对话。

    例外：``ToolInvocationError``（langgraph 对工具参数校验失败的包装）直接返回
    ``exc.message``——里面是逐字段校验反馈，LLM 靠它自我纠正畸形参数（如
    ``hours="notanint"``），不应被降级成占位文案。
    """
    if isinstance(exc, ToolInvocationError):
        return exc.message
    logger.warning("Tool execution failed: %s", type(exc).__name__)
    return "工具执行失败。"


def _route_after_detect(state: BotState) -> str:
    """Deterministic 3-way route（判定表单一来源见 bot.core.utils.routing）。

    - should_respond → describe_image (vision for image turns, no-op for text) → call_llm
    - non-replied text → summarize (context + compression + single-record index)
    - non-replied media (image group non-@ / file / audio / video) → END
    """
    return route_after_detect(
        state.get("should_respond", False),
        state.get("content_kind", ""),
    ) or END


def _route_after_llm(state: BotState) -> str:
    """call_llm 后路由：末条消息带 tool_calls → tools（ToolNode），否则 → summarize。"""
    last = state["messages"][-1]
    return "tools" if getattr(last, "tool_calls", None) else "summarize"


async def create_graph(
    llm: ChatOpenAI,
    config: BotConfig,
    db_dir: str = "db",
    rag_service=None,
    memory_store=None,
    vision_service=None,
    mcp_tools=None,
) -> tuple[CompiledStateGraph, AsyncSqliteSaver]:
    """Build and compile the conversation graph.

    Returns ``(graph, checkpointer)`` so the caller can manage the
    checkpointer's lifecycle.
    """
    tools = build_tools(
        rag_service=rag_service, memory_store=memory_store, mcp_tools=mcp_tools,
    )
    use_memory = memory_store is not None
    use_mcp = bool(mcp_tools)

    builder = StateGraph(BotState)
    builder.add_node("detect_intent", detect_intent)
    builder.add_node(
        "call_llm", partial(
            call_llm_node,
            llm=llm,
            tools=tools,
            use_memory=use_memory,
            use_mcp=use_mcp,
            bot_config=config,
        )
    )
    builder.add_node("summarize", partial(summarize_node, llm=llm, bot_config=config))
    builder.add_node("index_turn", partial(index_turn_node, rag_service=rag_service))
    builder.add_node("describe_image", partial(describe_image_node, vision_service=vision_service))
    builder.add_node("tools", ToolNode(tools, handle_tool_errors=_tool_error_message))

    builder.add_edge(START, "detect_intent")
    builder.add_conditional_edges("detect_intent", _route_after_detect)
    builder.add_conditional_edges("call_llm", _route_after_llm)
    builder.add_edge("tools", "call_llm")
    builder.add_edge("describe_image", "call_llm")
    builder.add_edge("summarize", "index_turn")
    builder.add_edge("index_turn", END)

    checkpoint_path = os.path.join(db_dir, "checkpoint.sqlite")
    conn = await aiosqlite.connect(checkpoint_path)
    checkpointer = AsyncSqliteSaver(conn)
    graph = builder.compile(checkpointer=checkpointer)
    logger.info("LangGraph compiled with AsyncSqliteSaver checkpointing (db=%s)", checkpoint_path)
    return graph, checkpointer
