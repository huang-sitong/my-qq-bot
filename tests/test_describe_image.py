"""describe_image_node：视觉描述注入 HumanMessage（原位替换、降级保留占位符）+ 多模态分支。"""

import asyncio

from langchain_core.messages import HumanMessage

from bot.core.nodes.action_node.describe_image import (
    build_multimodal_content,
    describe_image_node,
)
from tests.fakes import FakeVisionService, make_state


def _state(msg, **overrides):
    return make_state(
        messages=[msg],
        content_kind="image",
        image_srcs=["u1", "u2"],
        **overrides,
    )


def _patch_download(monkeypatch, urls):
    async def fake_download(srcs, *, max_images, timeout):
        return list(urls)

    monkeypatch.setattr(
        "bot.core.nodes.action_node.describe_image.download_images_as_data_urls",
        fake_download,
    )


# --- 纯文本模式（现状） ---

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


def test_auto_reply_non_multimodal_keeps_placeholder_and_skips_vision():
    fake = FakeVisionService(["猫"])
    msg = HumanMessage(content="看 [图片]")
    result = asyncio.run(describe_image_node(_state(msg, auto_reply=True), fake))
    assert result == {"vision_desc": ""}
    assert fake.calls == 0


# --- 多模态模式（主 LLM 直接收图） ---

def test_multimodal_builds_content_array_and_vision_desc(monkeypatch):
    fake = FakeVisionService(["猫"])
    msg = HumanMessage(content="看 [图片]")
    _patch_download(monkeypatch, ["data:image/jpeg;base64,AAA"])
    result = asyncio.run(describe_image_node(_state(msg), fake, llm_multimodal=True))
    new_msg = result["messages"][0]
    assert isinstance(new_msg, HumanMessage)
    assert new_msg.id == msg.id
    assert new_msg.content == [
        {"type": "text", "text": "看 "},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAA"}},
    ]
    # 本地视觉仍产 vision_desc 供 RAG 索引（理解归主 LLM）
    assert result["vision_desc"] == "猫"


def test_multimodal_without_vision_service(monkeypatch):
    msg = HumanMessage(content="[图片]")
    _patch_download(monkeypatch, ["data:image/jpeg;base64,AAA"])
    result = asyncio.run(describe_image_node(_state(msg), None, llm_multimodal=True))
    assert result["vision_desc"] == ""
    assert result["messages"][0].content == [
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAA"}},
    ]


def test_auto_reply_multimodal_skips_vision_desc(monkeypatch):
    fake = FakeVisionService(["猫"])
    msg = HumanMessage(content="看 [图片]")
    _patch_download(monkeypatch, ["data:image/jpeg;base64,AAA"])
    result = asyncio.run(
        describe_image_node(_state(msg, auto_reply=True), fake, llm_multimodal=True)
    )
    assert result["vision_desc"] == ""
    assert fake.calls == 0
    assert result["messages"][0].content == [
        {"type": "text", "text": "看 "},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAA"}},
    ]


def test_multimodal_download_failed_but_vision_desc_indexes(monkeypatch):
    """图片下载全失败 → 占位符保留（LLM 看不到图），但 vision_desc 仍供 RAG 索引。"""
    fake = FakeVisionService(["猫"])
    msg = HumanMessage(content="看 [图片]")
    _patch_download(monkeypatch, [""])
    result = asyncio.run(describe_image_node(_state(msg), fake, llm_multimodal=True))
    assert result["messages"][0].content == [{"type": "text", "text": "看 [图片]"}]
    assert result["vision_desc"] == "猫"


def test_multimodal_all_failed_clears_stale_vision_desc(monkeypatch):
    fake = FakeVisionService(["", ""])
    msg = HumanMessage(content="看 [图片]")
    _patch_download(monkeypatch, ["", ""])
    assert asyncio.run(describe_image_node(_state(msg), fake, llm_multimodal=True)) == {"vision_desc": ""}


# --- build_multimodal_content 纯函数 ---

def test_build_multimodal_content_interleaves_text_and_images():
    urls = ["data:a", "data:b"]
    assert build_multimodal_content("看 [图片] 和 [图片]！", urls) == [
        {"type": "text", "text": "看 "},
        {"type": "image_url", "image_url": {"url": "data:a"}},
        {"type": "text", "text": " 和 "},
        {"type": "image_url", "image_url": {"url": "data:b"}},
        {"type": "text", "text": "！"},
    ]


def test_build_multimodal_content_appends_extra_images():
    urls = ["data:a", "data:b"]
    assert build_multimodal_content("[图片]", urls) == [
        {"type": "image_url", "image_url": {"url": "data:a"}},
        {"type": "image_url", "image_url": {"url": "data:b"}},
    ]


def test_build_multimodal_content_no_placeholder_appends_all():
    assert build_multimodal_content("纯文本", ["data:a"]) == [
        {"type": "text", "text": "纯文本"},
        {"type": "image_url", "image_url": {"url": "data:a"}},
    ]


def test_build_multimodal_content_no_images_keeps_text():
    assert build_multimodal_content("有 [图片] 占位", []) == [
        {"type": "text", "text": "有 [图片] 占位"},
    ]
