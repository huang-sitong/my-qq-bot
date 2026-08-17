"""路由判定单一来源：是否回复 / 是否入上下文。

锁定 context.utils.routing 的完整判定表，此处是唯一权威，
行为若偏离这里即为回归。
"""

import pytest

from context.utils.routing import (
    decide_reply,
    is_explicit_request,
    keep_in_context,
)
from domain.satori import ChannelType

# --- decide_reply ---


def test_is_explicit_request_direct_true():
    assert is_explicit_request(ChannelType.DIRECT, "bot1", "Bot", {}) is True


def test_is_explicit_request_mention_true():
    assert is_explicit_request(ChannelType.TEXT, "bot1", "Bot", {"bot1": "小助手"}) is True


def test_is_explicit_request_group_non_mention_false():
    assert is_explicit_request(ChannelType.TEXT, "bot1", "Bot", {}) is False

@pytest.mark.parametrize("kind", ["file", "audio", "video"])
def test_media_never_reply_even_direct_or_mention(kind):
    assert decide_reply(ChannelType.DIRECT, kind, "bot1", "Bot", {}) is False
    assert decide_reply(ChannelType.TEXT, kind, "bot1", "Bot", {"bot1": "Bot"}) is False


def test_direct_text_replies():
    assert decide_reply(ChannelType.DIRECT, "text", "bot1", "Bot", {}) is True


def test_direct_image_replies():
    assert decide_reply(ChannelType.DIRECT, "image", "bot1", "Bot", {}) is True


def test_group_mention_by_id_replies():
    assert decide_reply(ChannelType.TEXT, "text", "bot1", "Bot", {"bot1": "小助手"}) is True


def test_group_mention_by_name_replies():
    # bot_id 不在 map，但 bot_name 命中 → 昵称兜底
    assert decide_reply(ChannelType.TEXT, "text", "bot1", "小助手", {"10001": "小助手"}) is True


def test_group_mention_by_name_with_empty_bot_id():
    assert decide_reply(ChannelType.TEXT, "text", "", "小助手", {"10001": "小助手"}) is True


def test_group_mention_other_user_no_reply():
    assert decide_reply(ChannelType.TEXT, "text", "bot1", "Bot", {"10002": "张三"}) is False


def test_group_without_mention_does_not_reply():
    assert decide_reply(ChannelType.TEXT, "text", "bot1", "Bot", {}) is False


def test_group_image_without_mention_no_reply():
    assert decide_reply(ChannelType.TEXT, "image", "bot1", "Bot", {}) is False


def test_empty_mentions_with_empty_bot_id_no_reply():
    assert decide_reply(ChannelType.TEXT, "text", "", "Bot", {}) is False


# --- decide_reply with auto_reply ---

def test_auto_reply_group_text_replies():
    assert decide_reply(ChannelType.TEXT, "text", "bot1", "Bot", {}, auto_reply=True) is True


def test_auto_reply_group_image_replies():
    assert decide_reply(ChannelType.TEXT, "image", "bot1", "Bot", {}, auto_reply=True) is True


def test_auto_reply_media_still_never_replies():
    for kind in ("file", "audio", "video"):
        assert decide_reply(ChannelType.TEXT, kind, "bot1", "Bot", {}, auto_reply=True) is False


def test_auto_reply_off_is_default_no_change():
    # 不带 auto_reply 参数 → 既有行为（群聊非@不回复）
    assert decide_reply(ChannelType.TEXT, "text", "bot1", "Bot", {}) is False


def test_auto_reply_direct_unchanged():
    assert decide_reply(ChannelType.DIRECT, "text", "bot1", "Bot", {}, auto_reply=True) is True


# --- keep_in_context ---

def test_keep_when_replying():
    assert keep_in_context(True, "image") is True


def test_keep_non_reply_text():
    assert keep_in_context(False, "text") is True


def test_not_keep_non_reply_media():
    assert keep_in_context(False, "image") is False
    assert keep_in_context(False, "file") is False


def test_keep_mixed_image_text_without_reply():
    assert keep_in_context(False, "image", has_text=True) is True


def test_not_keep_pure_image_without_text():
    assert keep_in_context(False, "image", has_text=False) is False
