import logging
import os
from functools import partial

import aiosqlite
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from langgraph.prebuilt.tool_node import ToolInvocationError

from bot.package.config import BotConfig
from bot.package.orchestration.nodes import (
    call_llm_node,
    describe_image_node,
    skill_manager_node,
)
from bot.package.orchestration.state import BotState

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


def _route_after_llm(state: BotState) -> str:
    """call_llm 后路由：末条消息带 tool_calls → tools（ToolNode），否则 END。"""
    messages = state.get("messages") or []
    if not messages:
        return END
    last = messages[-1]
    return "tools" if getattr(last, "tool_calls", None) else END


def _describe_image_with_turn(node, **inject):
    """包装 describe_image 节点：从 run config 提取当轮 TurnInput 注入。

    TurnInput 经 ``config["configurable"]["turn_input"]`` 传入，不落库；未提供时节点按消息数推断。
    """

    async def wrapped(state, *, config=None):
        configurable = (config or {}).get("configurable") or {}
        turn = configurable.get("turn_input")
        return await node(state, turn=turn, **inject)

    return wrapped


async def create_graph(
    llm: ChatOpenAI,
    config: BotConfig,
    *,
    tools: list[BaseTool],
    db_dir: str = "db",
    rag_service=None,
    document_store=None,
    memory_store=None,
    vision_service=None,
    mcp_tools=None,
    skill_registry=None,
    file_sender=None,
) -> tuple[CompiledStateGraph, AsyncSqliteSaver]:
    """Build and compile the conversation graph.

    ``tools`` is required: the caller (``bot.package.core.boot`` or a test
    harness) is responsible for assembling the tool list via
    ``bot.package.tools.build_tools`` and injecting it. Orchestration must not
    depend on the tools package.

    Returns ``(graph, checkpointer)`` so the caller can manage the
    checkpointer's lifecycle.
    """
    use_memory = memory_store is not None
    use_mcp = bool(mcp_tools)
    use_bash = config.bash_enabled

    builder = StateGraph(BotState)
    builder.add_node(
        "call_llm", partial(
            call_llm_node,
            llm=llm,
            tools=tools,
            use_memory=use_memory,
            use_mcp=use_mcp,
            use_bash=use_bash,
            use_file_send=file_sender is not None,
            bot_config=config,
            skill_registry=skill_registry,
        )
    )
    builder.add_node("skill_manager", partial(skill_manager_node, skill_registry=skill_registry))
    builder.add_node("describe_image", _describe_image_with_turn(
        describe_image_node,
        vision_service=vision_service,
        llm_multimodal=config.llm_multimodal,
        max_images=config.vision_max_images,
        timeout=config.vision_timeout,
    ))
    builder.add_node("tools", ToolNode(tools, handle_tool_errors=_tool_error_message))

    builder.add_edge(START, "describe_image")
    builder.add_conditional_edges("call_llm", _route_after_llm)
    # 工具回环：每轮工具执行后先经 skill_manager 写回 active_skills，再重入 call_llm。
    # 逐轮接线（而非图末一次）保证每一轮 load_skill/unload_skill 调用都被处理——
    # 若只在整轮结束时处理一次，早期轮次的技能调用会被漏掉。
    builder.add_edge("tools", "skill_manager")
    builder.add_edge("skill_manager", "call_llm")
    builder.add_edge("describe_image", "call_llm")

    checkpoint_path = os.path.join(db_dir, "checkpoint.sqlite")
    conn = await aiosqlite.connect(checkpoint_path)
    serializer = JsonPlusSerializer(
        allowed_msgpack_modules=[
            ("domain.media", "ImageDescription"),
        ],
    )
    checkpointer = AsyncSqliteSaver(conn, serde=serializer)
    graph = builder.compile(checkpointer=checkpointer)
    logger.info("LangGraph compiled with AsyncSqliteSaver checkpointing (db=%s)", checkpoint_path)
    return graph, checkpointer
