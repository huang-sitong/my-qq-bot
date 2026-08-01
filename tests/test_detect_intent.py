"""detect_intent：llm_text（handler 注入的清洗文本）优先，raw_content 兜底。"""

import asyncio

from langchain_core.messages import HumanMessage

from bot.core.nodes.action_node.detect_intent import detect_intent
from tests.fakes import make_state


def test_uses_llm_text_when_present():
    state = make_state(
        llm_text="[图片]这是git",
        raw_content='<img src="x"/>这是git',
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
        raw_content='<img src="x"/>',
        channel_type=1,  # DIRECT
        bot_id="bot1",
        user_name="",
    )
    result = asyncio.run(detect_intent(state))
    assert result["should_respond"] is True
    assert result["messages"][0].content == ""


def test_falls_back_to_mention_strip_when_llm_text_absent():
    state = make_state(
        raw_content='<at id="bot1" name="Bot"/> 你好',
        channel_type=0,
        bot_id="bot1",
    )
    result = asyncio.run(detect_intent(state))
    assert result["should_respond"] is True
    assert result["messages"][0].content == "你好"


def test_group_without_mention_defers_to_router():
    state = make_state(
        llm_text="晚上吃什么",
        raw_content="晚上吃什么",
        channel_type=0,
        bot_id="bot1",
    )
    result = asyncio.run(detect_intent(state))
    assert result["should_respond"] is False
