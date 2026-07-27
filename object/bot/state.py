from typing import Annotated

from langchain_core.messages import BaseMessage, HumanMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


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
