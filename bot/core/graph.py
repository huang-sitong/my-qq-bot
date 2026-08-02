import logging
import os
from functools import partial

import aiosqlite
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from bot.core.nodes import (
    call_llm_node,
    describe_image_node,
    detect_intent,
    index_turn_node,
    summarize_node,
    tool_node,
)
from bot.core.utils.routing import route_after_detect
from common import BotConfig
from object.bot.state import BotState

logger = logging.getLogger(__name__)


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


async def create_graph(
    llm: ChatOpenAI,
    config: BotConfig,
    db_dir: str = "db",
    rag_service=None,
    memory_store=None,
    vision_service=None,
) -> tuple[CompiledStateGraph, AsyncSqliteSaver]:
    """Build and compile the conversation graph.

    Returns ``(graph, checkpointer)`` so the caller can manage the
    checkpointer's lifecycle.
    """
    builder = StateGraph(BotState)
    builder.add_node("detect_intent", detect_intent)
    builder.add_node(
        "call_llm", partial(
            call_llm_node,
            llm=llm,
            rag_service=rag_service,
            memory_store=memory_store,
            bot_config=config,
        )
    )
    builder.add_node("summarize", partial(summarize_node, llm=llm, bot_config=config))
    builder.add_node("index_turn", partial(index_turn_node, rag_service=rag_service))
    builder.add_node("tool_node", partial(tool_node, rag_service=rag_service, memory_store=memory_store))
    builder.add_node("describe_image", partial(describe_image_node, vision_service=vision_service))

    builder.add_edge(START, "detect_intent")
    builder.add_conditional_edges("detect_intent", _route_after_detect)
    builder.add_conditional_edges(
        "call_llm",
        lambda s: "tool_node" if getattr(s["messages"][-1], "tool_calls", None) else "summarize",
    )
    builder.add_edge("tool_node", "call_llm")
    builder.add_edge("describe_image", "call_llm")
    builder.add_edge("summarize", "index_turn")
    builder.add_edge("index_turn", END)

    checkpoint_path = os.path.join(db_dir, "checkpoint.sqlite")
    conn = await aiosqlite.connect(checkpoint_path)
    checkpointer = AsyncSqliteSaver(conn)
    graph = builder.compile(checkpointer=checkpointer)
    logger.info("LangGraph compiled with AsyncSqliteSaver checkpointing (db=%s)", checkpoint_path)
    return graph, checkpointer
