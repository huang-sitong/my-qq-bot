"""当轮输入 — 非持久的 TurnInput 领域对象。

与 LangGraph 状态投影（``bot.package.orchestration.state.BotState``）分离：
TurnInput 仅承载当轮消息分类与视觉元数据，不落库、不参与摘要，仅供
describe_image 等当轮节点消费。持久字段仍在编排层 BotState。

此分离解决 BotState 膨胀与跨上下文耦合问题，并保持会话领域模型框架无关。
"""

from dataclasses import dataclass

from bot.package.domain import ImageDescription


@dataclass(frozen=True)
class TurnInput:
    channel_type: int
    bot_id: str
    auto_reply: bool
    content_kind: str
    has_text: bool
    llm_text: str
    clean_text: str
    vision_target_count: int
    vision_desc: list[ImageDescription]
    mentions: dict[str, str]
