from typing import Annotated

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from domain.bot.vision import ImageDescription


class BotState(TypedDict):
    """State of the conversation graph.

    ``messages`` uses the ``add_messages`` reducer so that each node
    only returns the *new* messages to append. Old messages are
    automatically checkpointed by SqliteSaver.

    ``should_respond`` is set by ``detect_intent`` deterministically
    (text/image reply on DIRECT or @-mention; file/audio/video never
    reply). No downstream node overrides it.

    ``clean_text`` 由 handler 预计算（``parse_content`` → ``ParsedContent.clean_text``）
    注入，供 RAG 索引（``index_turn``）直接消费，避免图内每轮重复解析。
    """
    messages: Annotated[list[BaseMessage], add_messages]
    persona: str
    conversation_summary: str   # progressive summary of older messages (dynamic inject)
    thread_id: str        # checkpoint isolation key = platform:guild:channel
    channel_id: str       # Satori channel id（send_file 等工具定位当前会话）
    reply_text: str
    should_respond: bool
    bot_name: str
    tool_rounds: int       # 工具调用轮次计数（call_llm 递增，工具回环上限）
    active_skills: list[str]  # 已激活技能名（skill_manager 写、build_system_messages 注入正文；handler 绝不注入）
    # --- Fields for detect_intent node ---
    channel_type: int       # ChannelType enum value (0=TEXT, 1=DIRECT)
    bot_id: str             # bot's own user ID for @-mention detection
    auto_reply: bool        # 群聊非@文本/图片是否直接回复（handler 从 config 注入，detect_intent 消费）
    # --- Message classification (computed in MessageHandler, ingress) ---
    content_kind: str       # domain.bot.content.MessageKind.value: "text"/"image"/"file"/"audio"/"video"
    has_text: bool         # handler 从 ParsedContent.has_text 注入，供图文混合入上下文
    llm_text: str           # media→占位符、@→@昵称(id)/所有成员 — HumanMessage content
    clean_text: str         # 剥全部标签、unescape、折叠空白（RAG 索引用，handler 预计算）
    vision_desc: list[ImageDescription]  # 本轮每张图片的 src→描述（见 domain.bot.vision）
    mentions: dict[str, str]   # 顶层 @ 提及 {id: 昵称}（detect_intent 判定用）
