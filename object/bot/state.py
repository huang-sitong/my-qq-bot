from typing import Annotated

from langchain_core.messages import BaseMessage, HumanMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class BotState(TypedDict):
    """State of the conversation graph.

    ``messages`` uses the ``add_messages`` reducer so that each node
    only returns the *new* messages to append. Old messages are
    automatically checkpointed by SqliteSaver.

    ``should_respond`` is set by ``detect_intent`` (fast path: DIRECT
    or @-mention) and may be overridden by ``router_node`` (LLM
    name-mention fallback for group chats).
    """
    messages: Annotated[list[BaseMessage], add_messages]
    persona: str
    conversation_summary: str   # progressive summary of older messages (dynamic inject)
    session_id: str
    thread_id: str        # checkpoint isolation key = platform:guild:channel
    user_id: str          # 当前消息发送者的用户 ID（记忆工具按用户维度存取）
    new_message: HumanMessage
    reply_text: str
    should_respond: bool
    bot_name: str
    tool_rounds: int       # 工具调用轮次计数（call_llm 递增，工具回环上限）
    # --- Fields for detect_intent node ---
    channel_type: int       # ChannelType enum value (0=TEXT, 1=DIRECT)
    bot_id: str             # bot's own user ID for @-mention detection
    raw_content: str        # original message before mention-stripping
    user_name: str          # sender's display name (for group chat attribution)
