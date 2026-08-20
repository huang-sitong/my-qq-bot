"""确定性回复判定（LLM router 已移除后的唯一判定表）。

群聊 @ 判定按顶层 at 提及集合（``{id: 昵称}``，parse_mentions 产出）。
纯函数，不 import langgraph；供 bot/core/router 与 reply_policy 消费。
"""

from bot.package.conversation.content import MessageKind

DIRECT_CHANNEL_TYPE = 1  # inline to avoid utils -> platform dependency (platform owns canonical value)

# 永不回复的媒体类型（file/audio/video，即使私聊/@ 也盖不过）
NON_REPLY_KINDS = frozenset({
    MessageKind.FILE.value,
    MessageKind.AUDIO.value,
    MessageKind.VIDEO.value,
})


def is_explicit_request(channel_type: int, bot_id: str, bot_name: str, mentions: dict) -> bool:
    """私聊或群聊顶层@bot 视为显式请求，永远绕过 auto_reply 随机门。"""
    if channel_type == DIRECT_CHANNEL_TYPE:
        return True
    mentioned_names = set(mentions.values())
    return bool(bot_id in mentions or (bot_name and bot_name in mentioned_names))


def decide_reply(channel_type: int, content_kind: str, bot_id: str,
                 bot_name: str, mentions: dict, auto_reply: bool = False) -> bool:
    """should_respond：媒体永不回复；私聊回复；群聊按顶层提及判定（id 为主、昵称兜底）；
    auto_reply=True 时群聊非@文本/图片也回复（媒体仍排除）。"""
    if content_kind in NON_REPLY_KINDS:
        return False
    if channel_type == DIRECT_CHANNEL_TYPE:
        return True
    if auto_reply:
        return True
    mentioned_names = set(mentions.values())
    return bool(bot_id in mentions or (bot_name and bot_name in mentioned_names))


def keep_in_context(should_respond: bool, content_kind: str, has_text: bool = False) -> bool:
    """回复轮必入上下文；非回复文本/图文混合入上下文；纯媒体不入。"""
    return should_respond or content_kind == MessageKind.TEXT.value or has_text
