"""detect_intent：确定性路由 + llm_text 优先的消息构造。

- should_respond：text/image 对 DIRECT/@ 回复；file/audio/video 一律不回复
- 非回复媒体不入上下文（messages 为空），非回复文本入上下文
- HumanMessage 用 handler 注入的 llm_text（每轮必注入，无兜底）
"""

import asyncio

import pytest
from langchain_core.messages import HumanMessage

from bot.core.nodes.action_node.detect_intent import detect_intent
from tests.fakes import make_state


def test_uses_llm_text_when_present():
    state = make_state(
        llm_text="[图片]这是git",
        channel_type=0,
        bot_id="bot1",
        user_name="张三",
    )
    result = asyncio.run(detect_intent(state))
    msg = result["messages"][0]
    assert isinstance(msg, HumanMessage)
    assert msg.content == "[图片]这是git"
    assert msg.name == "张三"


def test_image_only_empty_llm_text_is_preserved():
    state = make_state(
        llm_text="",
        content_kind="image",
        channel_type=1,  # DIRECT
        bot_id="bot1",
        user_name="",
    )
    result = asyncio.run(detect_intent(state))
    assert result["should_respond"] is True
    assert result["messages"][0].content == ""


def test_absent_llm_text_yields_empty_content():
    state = make_state(
        channel_type=0,
        bot_id="bot1",
        user_name="",
    )
    result = asyncio.run(detect_intent(state))
    assert result["should_respond"] is False
    assert result["messages"][0].content == ""


# ----------------------------------------------------------------------
# 确定性判定树
# ----------------------------------------------------------------------

def test_group_without_mention_does_not_respond():
    state = make_state(
        llm_text="晚上吃什么",
        channel_type=0,
        bot_id="bot1",
    )
    result = asyncio.run(detect_intent(state))
    assert result["should_respond"] is False


def test_group_text_without_mention_added_to_context():
    state = make_state(
        llm_text="晚上吃什么",
        channel_type=0,
        bot_id="bot1",
    )
    result = asyncio.run(detect_intent(state))
    assert result["should_respond"] is False
    assert len(result["messages"]) == 1  # 非回复文本仍入上下文


def test_group_at_mention_responds():
    state = make_state(
        llm_text="你好",
        content_kind="text",
        channel_type=0,
        bot_id="bot1",
        mentions={"bot1": "Bot"},
    )
    result = asyncio.run(detect_intent(state))
    assert result["should_respond"] is True


@pytest.mark.parametrize("kind", ["file", "audio", "video"])
def test_media_never_responds_even_in_direct(kind):
    state = make_state(
        content_kind=kind,
        channel_type=1,  # DIRECT 也盖不过媒体门
        bot_id="bot1",
    )
    result = asyncio.run(detect_intent(state))
    assert result["should_respond"] is False
    assert result["messages"] == []  # 不入上下文


def test_media_never_responds_even_with_mention():
    state = make_state(
        content_kind="file",
        channel_type=0,
        bot_id="bot1",
        mentions={"bot1": "Bot"},
    )
    result = asyncio.run(detect_intent(state))
    assert result["should_respond"] is False
    assert result["messages"] == []


def test_image_in_direct_responds():
    state = make_state(
        content_kind="image",
        llm_text="",
        channel_type=1,  # DIRECT
        bot_id="bot1",
    )
    result = asyncio.run(detect_intent(state))
    assert result["should_respond"] is True


def test_image_in_group_without_at_does_not_respond():
    state = make_state(
        content_kind="image",
        channel_type=0,
        bot_id="bot1",
    )
    result = asyncio.run(detect_intent(state))
    assert result["should_respond"] is False
    assert result["messages"] == []  # 不入上下文、不索引


def test_group_image_with_text_without_at_added_to_context():
    state = make_state(
        llm_text="看图 [图片]",
        clean_text="看图",
        content_kind="image",
        channel_type=0,
        bot_id="bot1",
        has_text=True,
    )
    result = asyncio.run(detect_intent(state))
    assert result["should_respond"] is False
    assert len(result["messages"]) == 1


def test_group_name_only_mention_responds_with_empty_bot_id():
    state = make_state(
        llm_text="@小助手(10001) 你好",
        content_kind="text",
        channel_type=0,
        bot_id="",
        bot_name="小助手",
        mentions={"10001": "小助手"},
    )
    result = asyncio.run(detect_intent(state))
    assert result["should_respond"] is True


def test_group_non_at_text_responds_when_auto_reply():
    state = make_state(
        llm_text="晚上吃什么",
        content_kind="text",
        channel_type=0,
        bot_id="bot1",
        auto_reply=True,
    )
    result = asyncio.run(detect_intent(state))
    assert result["should_respond"] is True
    assert len(result["messages"]) == 1  # 回复轮入上下文


def test_group_non_at_image_responds_when_auto_reply():
    state = make_state(
        content_kind="image",
        channel_type=0,
        bot_id="bot1",
        auto_reply=True,
    )
    result = asyncio.run(detect_intent(state))
    assert result["should_respond"] is True
    assert len(result["messages"]) == 1


def test_auto_reply_absent_defaults_to_off():
    # make_state 不含 auto_reply → .get 兜底 False，既有行为不变
    state = make_state(
        llm_text="晚上吃什么",
        channel_type=0,
        bot_id="bot1",
    )
    result = asyncio.run(detect_intent(state))
    assert result["should_respond"] is False
