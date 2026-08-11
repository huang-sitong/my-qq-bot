"""确定性回复判定（LLM router 已移除后的唯一判定表）。

detect_intent 与 graph._route_after_detect 共同消费，消除双处同步。
群聊 @ 判定按顶层 at 提及集合（``{id: 昵称}``，parse_mentions 产出）。
纯函数：不 import langgraph；route_after_detect 返回 None 表示 END，由 graph 映射。
"""

from object.bot.content import MessageKind
from object.satori import ChannelType

# 永不回复的媒体类型（file/audio/video，即使私聊/@ 也盖不过）
NON_REPLY_KINDS = frozenset({
    MessageKind.FILE.value,
    MessageKind.AUDIO.value,
    MessageKind.VIDEO.value,
})


def decide_reply(channel_type: int, content_kind: str, bot_id: str,
                 bot_name: str, mentions: dict, auto_reply: bool = False) -> bool:
    """should_respond：媒体永不回复；私聊回复；群聊按顶层提及判定（id 为主、昵称兜底）；
    auto_reply=True 时群聊非@文本/图片也回复（媒体仍排除）。"""
    if content_kind in NON_REPLY_KINDS:
        return False
    if channel_type == ChannelType.DIRECT:
        return True
    if auto_reply:
        return True
    mentioned_names = set(mentions.values())
    return bool(bot_id in mentions or (bot_name and bot_name in mentioned_names))


def keep_in_context(should_respond: bool, content_kind: str) -> bool:
    """非回复媒体不入上下文（占位符防污染）；非回复文本仍入上下文待压缩。"""
    return should_respond or content_kind == MessageKind.TEXT.value


def route_after_detect(should_respond: bool, content_kind: str) -> str | None:
    """detect_intent 之后的三路路径；None 表示 END（由 graph 映射）。"""
    if should_respond:
        return "describe_image"
    if content_kind == MessageKind.TEXT.value:
        return "summarize"
    return None
