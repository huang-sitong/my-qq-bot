"""describe_image_node：视觉描述注入 HumanMessage（原位替换、降级保留占位符）。"""

import asyncio

from langchain_core.messages import HumanMessage

from bot.core.nodes.action_node.describe_image import describe_image_node
from tests.fakes import FakeVisionService, make_state


def _state(msg, **overrides):
    return make_state(
        messages=[msg],
        content_kind="image",
        image_srcs=["u1", "u2"],
        **overrides,
    )


def test_noop_when_vision_disabled():
    msg = HumanMessage(content="[图片]")
    assert asyncio.run(describe_image_node(_state(msg), None)) == {}


def test_noop_without_image_srcs():
    fake = FakeVisionService()
    state = make_state(messages=[HumanMessage(content="你好")], content_kind="text")
    assert asyncio.run(describe_image_node(state, fake)) == {}
    assert fake.calls == 0


def test_replaces_placeholders_in_order():
    fake = FakeVisionService(["猫", "狗"])
    msg = HumanMessage(content="看 [图片] 和 [图片]")
    result = asyncio.run(describe_image_node(_state(msg), fake))
    new_msg = result["messages"][0]
    assert isinstance(new_msg, HumanMessage)
    assert new_msg.content == "看 [图片：猫] 和 [图片：狗]"
    assert new_msg.id == msg.id  # 原位替换，不产生重复消息
    assert result["vision_desc"] == "猫；狗"


def test_partial_failure_keeps_placeholder():
    fake = FakeVisionService(["", "狗"])
    msg = HumanMessage(content="看 [图片] 和 [图片]")
    result = asyncio.run(describe_image_node(_state(msg), fake))
    assert result["messages"][0].content == "看 [图片] 和 [图片：狗]"
    assert result["vision_desc"] == "狗"


def test_all_failed_clears_stale_vision_desc():
    fake = FakeVisionService(["", ""])
    msg = HumanMessage(content="[图片]")
    assert asyncio.run(describe_image_node(_state(msg), fake)) == {"vision_desc": ""}
