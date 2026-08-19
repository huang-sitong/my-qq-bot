"""auto_reply 随机/冷却策略纯函数测试。"""

from bot.package.platform.satori import ChannelType
from bot.package.utils.reply_policy import should_allow_auto_reply


def test_disabled_never_allows():
    assert should_allow_auto_reply(
        ChannelType.TEXT, {}, "bot1", "Bot", False, True, 0.0, 0.3,
    ) is False


def test_explicit_direct_bypasses_gate():
    assert should_allow_auto_reply(
        ChannelType.DIRECT, {}, "bot1", "Bot", True, True, 0.9, 0.3,
    ) is False


def test_explicit_mention_bypasses_gate():
    assert should_allow_auto_reply(
        ChannelType.TEXT, {"bot1": "Bot"}, "bot1", "Bot", True, True, 0.9, 0.3,
    ) is False


def test_cooldown_blocks_auto_reply():
    assert should_allow_auto_reply(
        ChannelType.TEXT, {}, "bot1", "Bot", True, False, 0.0, 0.3,
    ) is False


def test_random_below_rate_allows():
    assert should_allow_auto_reply(
        ChannelType.TEXT, {}, "bot1", "Bot", True, True, 0.1, 0.3,
    ) is True


def test_random_above_rate_blocks():
    assert should_allow_auto_reply(
        ChannelType.TEXT, {}, "bot1", "Bot", True, True, 0.5, 0.3,
    ) is False
