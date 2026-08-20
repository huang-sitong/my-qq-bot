"""LangGraph 会话状态投影（BotState）。

这是领域会话对象在 LangGraph 工作流中的基础设施侧投影，不是会话领域模型：
它拥有对 ``langchain_core`` / ``langgraph`` 的类型依赖，并只负责描述
checkpoint 的 channel schema。领域层（``bot.package.conversation``）不导入
任何 LangChain / LangGraph 类型；节点通过本投影读写图状态。

仅保留需持久化的 10 字段；当轮输入（channel_type/content_kind/vision_* 等）
已迁移至 ``conversation.turn.TurnInput``，不再落库。

兼容：旧 checkpoint 仍含当轮字段，TypedDict total=False 允许额外键存在，
节点通过 ``state.get("vision_target_count")`` 回退读取，逐步迁移。
"""

from typing import Annotated

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class BotState(TypedDict, total=False):
    """State of the conversation graph — 持久态（落 checkpoint）。"""

    messages: Annotated[list[BaseMessage], add_messages]
    persona: str
    conversation_summary: str  # progressive summary of older messages (dynamic inject)
    thread_id: str  # checkpoint isolation key = platform:guild:channel
    channel_id: str  # Satori channel id（send_file 等工具定位当前会话）
    reply_text: str
    should_respond: bool
    bot_name: str
    tool_rounds: int  # 工具调用轮次计数（call_llm 递增，工具回环上限）
    active_skills: list[str]  # 已激活技能名（skill_manager 写、build_system_messages 注入正文；handler 绝不注入）


__all__ = ["BotState"]
