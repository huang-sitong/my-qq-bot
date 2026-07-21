import logging

from typing import Annotated

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from typing_extensions import TypedDict

from bot.ws.client import SatoriClient
from data_object.satori.api import MESSAGE_CREATE, MessageCreateParams

logger = logging.getLogger(__name__)


class BotState(TypedDict):
    """State of the conversation graph.

    ``messages`` uses the ``add_messages`` reducer so that each node
    only returns the *new* messages to append. Old messages are
    automatically checkpointed by SqliteSaver.

    ``should_respond`` is set by the handler (fast path) or by the
    router node (LLM-based name-mention detection for group chats).
    """
    messages: Annotated[list[BaseMessage], add_messages]
    persona: str
    user_memories: str
    session_id: str
    new_message: HumanMessage
    reply_text: str
    guild_id: str
    channel_id: str
    should_respond: bool
    bot_name: str


ROUTER_PROMPT = """你是一个消息路由判断器，判断群聊消息是否明确提到了机器人。

机器人的名字叫 "{bot_name}"。

如果消息满足以下任一条件，返回 true：
1. 消息中明确提到了机器人的名字（如 "{bot_name}今天吃什么？"）
2. 消息中以 "/" 开头（命令调用）
3. 消息明显是在向机器人提问或对话（如问候机器人）
4. 消息中提到了"机器人"、"bot"、"助手"等指向AI助手的称呼

如果消息是群聊中成员之间的一般对话，没有指向机器人，返回 false。

只返回 true 或 false，不要返回其他内容。"""


async def create_graph(llm: ChatOpenAI, client: SatoriClient) -> CompiledStateGraph:
    """Build and compile the conversation graph.

    Graph structure::

        START -> load_context -> call_llm -> send_reply -> END

    Each node is a closure capturing the ``llm`` and ``client``
    dependencies.
    """
    # ---- Node definitions (closures over llm / client) ----

    def load_context(state: BotState) -> dict:
        """Inject persona (+ user memories) as SystemMessage and append the new user message."""
        updates: list[BaseMessage] = []
        has_persona = any(isinstance(m, SystemMessage) for m in state["messages"])
        if not has_persona:
            system_content = state["persona"]
            memories = state.get("user_memories", "").strip()
            if memories:
                system_content += f"\n\n关于当前用户已知的信息：\n{memories}"
            updates.append(SystemMessage(content=system_content))
        updates.append(state["new_message"])
        return {"messages": updates}

    async def call_llm(state: BotState) -> dict:
        """Call the LLM and extract reply text."""
        try:
            response = await llm.ainvoke(state["messages"])
            reply = response.content if hasattr(response, "content") else str(response)
        except Exception as exc:
            if isinstance(exc, type(TimeoutError(""))) or "Timeout" in type(exc).__name__:
                logger.warning("LLM call timed out for session %s", state["session_id"])
            else:
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

    async def router_node(state: BotState) -> dict:
        """Decide whether the bot should respond.

        Fast path (should_respond already True from handler): no-op.
        Slow path: call LLM to check if the message mentions the bot by name.
        """
        if state.get("should_respond", True):
            return {}
        prompt = ROUTER_PROMPT.format(bot_name=state.get("bot_name", ""))
        try:
            response = await llm.ainvoke([
                SystemMessage(content=prompt),
                HumanMessage(content=f"消息内容：{state['new_message'].content}"),
            ])
            should_respond = "true" in response.content.strip().lower()
        except Exception:
            logger.warning("Router LLM call failed for session %s", state["session_id"])
            should_respond = False
        logger.debug("Router decision: should_respond=%s", should_respond)
        return {"should_respond": should_respond}

    # ---- Build graph ----

    builder = StateGraph(BotState)
    builder.add_node("router", router_node)
    builder.add_node("load_context", load_context)
    builder.add_node("call_llm", call_llm)
    builder.add_node("send_reply", send_reply)

    # START → router → conditional → load_context or END
    builder.add_edge(START, "router")
    builder.add_conditional_edges(
        "router",
        lambda s: "load_context" if s.get("should_respond", True) else END,
    )
    builder.add_edge("load_context", "call_llm")
    builder.add_edge("call_llm", "send_reply")
    builder.add_edge("send_reply", END)

    import aiosqlite

    conn = await aiosqlite.connect("bot_memory.sqlite")
    checkpointer = AsyncSqliteSaver(conn)
    graph = builder.compile(checkpointer=checkpointer)
    logger.info("LangGraph compiled with AsyncSqliteSaver checkpointing")
    return graph
