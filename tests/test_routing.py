"""路由判定单一来源：是否回复 / 是否入上下文 / detect_intent 后三路路径。

锁定 bot.core.utils.routing 的完整判定表——detect_intent 与 graph 共同消费，
此处是唯一权威，行为若偏离这里即为回归。
"""

import pytest

from bot.core.utils.routing import decide_reply, keep_in_context, route_after_detect
from object.satori import ChannelType


# --- decide_reply ---

@pytest.mark.parametrize("kind", ["file", "audio", "video"])
def test_media_never_reply_even_direct_or_mention(kind):
    assert decide_reply(ChannelType.DIRECT, kind, "bot1", "Bot", {}) is False
    assert decide_reply(ChannelType.TEXT, kind, "bot1", "Bot", {"Bot": "bot1"}) is False


def test_direct_text_replies():
    assert decide_reply(ChannelType.DIRECT, "text", "bot1", "Bot", {}) is True


def test_direct_image_replies():
    assert decide_reply(ChannelType.DIRECT, "image", "bot1", "Bot", {}) is True


def test_group_mention_by_id_replies():
    assert decide_reply(ChannelType.TEXT, "text", "bot1", "Bot", {"小助手": "bot1"}) is True


def test_group_mention_by_name_replies():
    # bot_id 不在 map，但 bot_name 命中 → 昵称兜底
    assert decide_reply(ChannelType.TEXT, "text", "bot1", "小助手", {"小助手": "10001"}) is True


def test_group_mention_by_name_with_empty_bot_id():
    assert decide_reply(ChannelType.TEXT, "text", "", "小助手", {"小助手": "10001"}) is True


def test_group_mention_other_user_no_reply():
    assert decide_reply(ChannelType.TEXT, "text", "bot1", "Bot", {"张三": "10002"}) is False


def test_group_without_mention_does_not_reply():
    assert decide_reply(ChannelType.TEXT, "text", "bot1", "Bot", {}) is False


def test_group_image_without_mention_no_reply():
    assert decide_reply(ChannelType.TEXT, "image", "bot1", "Bot", {}) is False


def test_empty_mentions_with_empty_bot_id_no_reply():
    assert decide_reply(ChannelType.TEXT, "text", "", "Bot", {}) is False


# --- keep_in_context ---

def test_keep_when_replying():
    assert keep_in_context(True, "image") is True


def test_keep_non_reply_text():
    assert keep_in_context(False, "text") is True


def test_not_keep_non_reply_media():
    assert keep_in_context(False, "image") is False
    assert keep_in_context(False, "file") is False


# --- route_after_detect ---

def test_reply_routes_to_describe_image():
    assert route_after_detect(True, "text") == "describe_image"


def test_reply_image_routes_to_describe_image():
    assert route_after_detect(True, "image") == "describe_image"


def test_non_reply_text_routes_to_summarize():
    assert route_after_detect(False, "text") == "summarize"


def test_non_reply_media_routes_to_none():
    assert route_after_detect(False, "image") is None
    assert route_after_detect(False, "file") is None
