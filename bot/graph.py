import logging
import sqlite3
from typing import Annotated

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from typing_extensions import TypedDict

from bot.client import SatoriClient
from data_object.satori.api import MESSAGE_CREATE, MessageCreateParams

logger = logging.getLogger(__name__)


class BotState(TypedDict):
    """State of the conversation graph.

    ``messages`` uses the ``add_messages`` reducer so that each node
    only returns the *new* messages to append. Old messages are
    automatically checkpointed by SqliteSaver.

    The ``session_id`` doubles as the LangGraph ``thread_id`` for
    checkpoint isolation.
    """
    messages: Annotated[list[BaseMessage], add_messages]
    persona: str
    session_id: str
    new_message: HumanMessage
    reply_text: str
    guild_id: str
    channel_id: str


def create_graph(llm: ChatOpenAI, client: SatoriClient) -> CompiledStateGraph:
    """Build and compile the conversation graph.

    Graph structure::

        START -> load_context -> call_llm -> send_reply -> END

    Each node is a closure capturing the ``llm`` and ``client``
    dependencies.
    """
    # ---- Node definitions (closures over llm / client) ----

    def load_context(state: BotState) -> dict:
        """Inject persona as SystemMessage and append the new user message."""
        updates: list[BaseMessage] = []
        has_persona = any(isinstance(m, SystemMessage) for m in state["messages"])
        if not has_persona:
            updates.append(SystemMessage(content=state["persona"]))
        updates.append(state["new_message"])
        return {"messages": updates}

    async def call_llm(state: BotState) -> dict:
        """Call the LLM and extract reply text."""
        try:
            response = await llm.ainvoke(state["messages"])
            reply = response.content if hasattr(response, "content") else str(response)
        except TimeoutError:
            logger.warning("LLM call timed out for session %s", state["session_id"])
            reply = "我暂时无法思考，请稍后再试"
        except Exception:
            logger.exception("LLM call failed for session %s", state["session_id"])
            reply = "我暂时无法思考，请稍后再试"

        return {"messages": [AIMessage(content=reply)], "reply_text": reply}

    async def send_reply(state: BotState) -> dict:
        """Send the reply text back to the source channel via Satori API."""
        try:
            params = MessageCreateParams(
                channel_id=state["channel_id"],
                content=state["reply_text"],
            )
            await client.call_api(MESSAGE_CREATE, params)
        except Exception:
            logger.exception(
                "Failed to send reply to channel %s (session %s)",
                state["channel_id"],
                state["session_id"],
            )
        return {}

    # ---- Build graph ----

    builder = StateGraph(BotState)
    builder.add_node("load_context", load_context)
    builder.add_node("call_llm", call_llm)
    builder.add_node("send_reply", send_reply)

    builder.add_edge(START, "load_context")
    builder.add_edge("load_context", "call_llm")
    builder.add_edge("call_llm", "send_reply")
    builder.add_edge("send_reply", END)

    conn = sqlite3.connect("bot_memory.sqlite", check_same_thread=False)
    checkpointer = SqliteSaver(conn)
    graph = builder.compile(checkpointer=checkpointer)
    logger.info("LangGraph compiled with SqliteSaver checkpointing")
    return graph
