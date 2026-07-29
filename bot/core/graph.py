import logging
import os
from functools import partial

import aiosqlite
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from bot.core.nodes import call_llm_node, detect_intent, router_node
from object.bot.state import BotState

logger = logging.getLogger(__name__)


async def create_graph(llm: ChatOpenAI, db_dir: str = "db") -> tuple[CompiledStateGraph, AsyncSqliteSaver]:
    """Build and compile the conversation graph.

    Returns ``(graph, checkpointer)`` so the caller can manage the
    checkpointer's lifecycle.
    """
    builder = StateGraph(BotState)
    builder.add_node("detect_intent", detect_intent)
    builder.add_node("router", partial(router_node, llm=llm))
    builder.add_node("call_llm", partial(call_llm_node, llm=llm))

    builder.add_edge(START, "detect_intent")
    builder.add_edge("detect_intent", "router")
    builder.add_conditional_edges(
        "router",
        lambda s: "call_llm" if s.get("should_respond", True) else END,
    )
    builder.add_edge("call_llm", END)

    checkpoint_path = os.path.join(db_dir, "checkpoint.sqlite")
    conn = await aiosqlite.connect(checkpoint_path)
    checkpointer = AsyncSqliteSaver(conn)
    graph = builder.compile(checkpointer=checkpointer)
    logger.info("LangGraph compiled with AsyncSqliteSaver checkpointing (db=%s)", checkpoint_path)
    return graph, checkpointer
