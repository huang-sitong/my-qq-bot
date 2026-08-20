import logging
import os
from functools import partial
from pathlib import Path

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
from bot.package.conversation.state import BotState
from bot.package.tools.domain import BashConfig
from bot.package.orchestration.nodes import (
    call_llm_node,
    describe_image_node,
    skill_manager_node,
)
from bot.package.utils.paths import PROJECT_ROOT

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


async def create_graph(
    llm: ChatOpenAI,
    config: BotConfig,
    db_dir: str = "db",
    tools: list[BaseTool] | None = None,
    rag_service=None,
    document_store=None,
    memory_store=None,
    vision_service=None,
    mcp_tools=None,
    skill_registry=None,
    file_sender=None,
) -> tuple[CompiledStateGraph, AsyncSqliteSaver]:
    """Build and compile the conversation graph.

    Returns ``(graph, checkpointer)`` so the caller can manage the
    checkpointer's lifecycle.
    """
    bash_config = BashConfig(
        enabled=config.bash_enabled,
        shell=config.bash_shell,
        timeout=config.bash_timeout,
        max_output=config.bash_max_output,
        allowed_roots=config.bash_allowed_roots,
        project_root=PROJECT_ROOT,
    )
    send_roots = [PROJECT_ROOT] + [
        Path(root).resolve() for root in config.bash_allowed_roots
    ]
    if tools is None:
        # 兼容旧调用方；目标架构由 bot.package.core.boot 装配后注入 tools。
        from bot.package.tools import build_tools

        tools = build_tools(
            rag_service=rag_service, document_store=document_store,
            memory_store=memory_store, mcp_tools=mcp_tools,
            skill_registry=skill_registry, bash_config=bash_config,
            file_sender=file_sender, send_roots=send_roots,
        )
    use_memory = memory_store is not None
    use_mcp = bool(mcp_tools)
    use_bash = bash_config.enabled

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
    builder.add_node("describe_image", partial(
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
    # 显式注册 checkpoint 中允许反序列化的自定义类型路径。
    # 老 checkpoint 可能存的是 vision.domain.ImageDescription（历史 re-export 路径），
    # 新 checkpoint 使用 domain.media.ImageDescription，两者都要放行以避免 serde 警告。
    serializer = JsonPlusSerializer(
        allowed_msgpack_modules=[
            ("domain.media", "ImageDescription"),
            ("vision.domain", "ImageDescription"),
        ],
    )
    checkpointer = AsyncSqliteSaver(conn, serde=serializer)
    graph = builder.compile(checkpointer=checkpointer)
    logger.info("LangGraph compiled with AsyncSqliteSaver checkpointing (db=%s)", checkpoint_path)
    return graph, checkpointer
