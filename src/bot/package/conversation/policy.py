"""会话回复 / 入上下文领域策略（Conversation ReplyPolicy）。

这是“是否回复”“是否保留进上下文”“auto_reply 随机/冷却门”三类会话规则的
唯一权威实现。规则属于会话领域而非技术工具，因此从 ``utils`` 迁入
``conversation``；旧 ``utils.routing`` / ``utils.reply_policy`` 垫片已删除。

纯领域约束：
- 只依赖 Python 标准库与本上下文内对象；
- 不 import LangChain / LangGraph / platform / utils，保持领域层框架无关。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from bot.package.conversation.content import MessageKind

if TYPE_CHECKING:
    from bot.package.conversation.message import IncomingMessage

# Satori ChannelType.DIRECT 在会话策略里的语义值：私聊通道。
# 领域不依赖平台；平台侧如需对齐直接从本模块导入。
DIRECT_CHANNEL_TYPE = 1

# 永不回复的媒体类型（file/audio/video，即使私聊/@ 也盖不过）
NON_REPLY_KINDS = frozenset({
    MessageKind.FILE.value,
    MessageKind.AUDIO.value,
    MessageKind.VIDEO.value,
})


def _mentions_bot(
    bot_id: str,
    bot_name: str,
    mentions: Mapping[str, str],
) -> bool:
    """顶层提及集合中是否命中 bot：id 为主，昵称兜底。"""
    mentioned_names = set(mentions.values())
    return bool(bot_id in mentions or (bot_name and bot_name in mentioned_names))


def is_explicit_request(
    channel_type: int,
    bot_id: str,
    bot_name: str,
    mentions: Mapping[str, str],
) -> bool:
    """私聊或群聊顶层@bot 视为显式请求，永远绕过 auto_reply 随机门。"""
    if channel_type == DIRECT_CHANNEL_TYPE:
        return True
    return _mentions_bot(bot_id, bot_name, mentions)


def decide_reply(
    channel_type: int,
    content_kind: str,
    bot_id: str,
    bot_name: str,
    mentions: Mapping[str, str],
    auto_reply: bool = False,
) -> bool:
    """should_respond 判定。

    - file/audio/video 永不回复；
    - 私聊回复；
    - 群聊按顶层提及判定（id 为主、昵称兜底）；
    - auto_reply=True 时群聊非@文本/图片也回复（媒体仍排除）。
    """
    if content_kind in NON_REPLY_KINDS:
        return False
    if channel_type == DIRECT_CHANNEL_TYPE:
        return True
    if auto_reply:
        return True
    return _mentions_bot(bot_id, bot_name, mentions)


def keep_in_context(
    should_respond: bool,
    content_kind: str,
    has_text: bool = False,
) -> bool:
    """回复轮必入上下文；非回复文本/图文混合入上下文；纯媒体不入。"""
    return should_respond or content_kind == MessageKind.TEXT.value or has_text


def should_allow_auto_reply(
    channel_type: int,
    mentions: Mapping[str, str],
    bot_id: str,
    bot_name: str,
    auto_reply_enabled: bool,
    cooldown_elapsed: bool,
    random_value: float,
    rate: float,
) -> bool:
    """auto_reply 随机/冷却门；random_value 由调用方注入，保持可测试。"""
    if not auto_reply_enabled:
        return False
    if is_explicit_request(channel_type, bot_id, bot_name, mentions):
        return False
    if not cooldown_elapsed:
        return False
    return random_value < rate


@dataclass(frozen=True)
class ReplyDecision:
    """一条入站消息经会话策略计算后的领域结果。"""

    should_respond: bool
    keep_in_context: bool


class ReplyPolicy:
    """会话策略领域服务。

    - ``evaluate`` 以 :class:`IncomingMessage` 为输入一次产出
      :class:`ReplyDecision`，供应用层流水线直接消费；
    - ``should_allow_auto_reply`` 供 worker 池做 auto_reply 随机/冷却门；
    - 底层判定逻辑在模块级纯函数（is_explicit_request / decide_reply /
      keep_in_context），测试可直接针对纯函数写用例。
    """

    @staticmethod
    def should_allow_auto_reply(
        channel_type: int,
        mentions: Mapping[str, str],
        bot_id: str,
        bot_name: str,
        auto_reply_enabled: bool,
        cooldown_elapsed: bool,
        random_value: float,
        rate: float,
    ) -> bool:
        return should_allow_auto_reply(
            channel_type,
            mentions,
            bot_id,
            bot_name,
            auto_reply_enabled,
            cooldown_elapsed,
            random_value,
            rate,
        )

    @staticmethod
    def evaluate(
        message: IncomingMessage,
        *,
        bot_id: str,
        bot_name: str,
        auto_reply: bool = False,
    ) -> ReplyDecision:
        """对一条归一化入站消息执行完整会话策略。"""
        should_respond = decide_reply(
            message.channel_type,
            message.content_kind,
            bot_id,
            bot_name,
            message.mentions,
            auto_reply,
        )
        keep = keep_in_context(
            should_respond,
            message.content_kind,
            message.has_text,
        )
        return ReplyDecision(
            should_respond=should_respond,
            keep_in_context=keep,
        )


__all__ = [
    "DIRECT_CHANNEL_TYPE",
    "NON_REPLY_KINDS",
    "ReplyDecision",
    "ReplyPolicy",
    "decide_reply",
    "is_explicit_request",
    "keep_in_context",
    "should_allow_auto_reply",
]
