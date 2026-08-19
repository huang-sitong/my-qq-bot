"""describe_image_node：逐消息图片元数据、逐图描述映射、多模态分支。"""

import asyncio

from langchain_core.messages import HumanMessage

from bot.package.orchestration.nodes.action_node.describe_image import (
    build_multimodal_content,
    describe_image_node,
)
from bot.package.vision.domain import ImageDescription
from tests.fakes import FakeVisionService, make_state


class _PerCallVision:
    def __init__(self, calls):
        self._calls = list(calls)

    async def describe_many(self, srcs):
        return list(self._calls.pop(0))


def _msg(content, srcs, user_id="u1", name="张三", auto_reply=None):
    kwargs = {"user_id": user_id, "image_srcs": srcs}
    if auto_reply is not None:
        kwargs["auto_reply"] = auto_reply
    return HumanMessage(
        content=content,
        name=name,
        additional_kwargs=kwargs,
    )


def _state(*messages, **overrides):
    return make_state(
        messages=list(messages),
        content_kind="image",
        vision_target_count=len(messages),
        **overrides,
    )


def _patch_download(monkeypatch, urls_by_src):
    async def fake_download(srcs, *, max_images, timeout):
        return [urls_by_src.get(src, "") for src in srcs]

    monkeypatch.setattr(
        "bot.package.orchestration.nodes.action_node.describe_image.download_images_as_data_urls",
        fake_download,
    )


# --- 纯文本模式（视觉服务） ---

def test_noop_when_vision_disabled():
    assert asyncio.run(
        describe_image_node(_state(_msg("[图片]", ["u1"])), None)
    ) == {}


def test_noop_without_image_metadata():
    fake = FakeVisionService()
    state = make_state(messages=[HumanMessage(content="你好")], content_kind="text")
    assert asyncio.run(describe_image_node(state, fake)) == {}
    assert fake.calls == 0


def test_replaces_placeholders_in_order():
    fake = FakeVisionService(["猫", "狗"])
    msg = _msg("看 [图片] 和 [图片]", ["u1", "u2"])
    result = asyncio.run(describe_image_node(_state(msg), fake))
    new_msg = result["messages"][0]
    assert isinstance(new_msg, HumanMessage)
    assert new_msg.content == "看 [图片：猫] 和 [图片：狗]"
    assert new_msg.id == msg.id
    assert new_msg.additional_kwargs["user_id"] == "u1"
    assert result["vision_desc"] == [
        ImageDescription("u1", "猫"),
        ImageDescription("u2", "狗"),
    ]


def test_partial_failure_keeps_placeholder_and_source_mapping():
    fake = FakeVisionService(["", "狗"])
    msg = _msg("看 [图片] 和 [图片]", ["u1", "u2"])
    result = asyncio.run(describe_image_node(_state(msg), fake))
    assert result["messages"][0].content == "看 [图片] 和 [图片：狗]"
    assert result["vision_desc"] == [
        ImageDescription("u1", ""),
        ImageDescription("u2", "狗"),
    ]


def test_all_failed_clears_stale_vision_desc():
    fake = FakeVisionService(["", ""])
    msg = _msg("[图片]", ["u1"])
    assert asyncio.run(describe_image_node(_state(msg), fake)) == {"vision_desc": []}


def test_auto_reply_non_multimodal_keeps_placeholder_and_skips_vision():
    fake = FakeVisionService(["猫"])
    msg = _msg("看 [图片]", ["u1"])
    result = asyncio.run(
        describe_image_node(_state(msg, auto_reply=True), fake)
    )
    assert result == {"vision_desc": []}
    assert fake.calls == 0


def test_per_message_auto_reply_metadata_overrides_state():
    fake = FakeVisionService(["猫"])
    msg = _msg("[图片]", ["u1"], auto_reply=False)
    result = asyncio.run(
        describe_image_node(_state(msg, auto_reply=True), fake)
    )
    assert result["messages"][0].content == "[图片：猫]"
    assert result["vision_desc"] == [ImageDescription("u1", "猫")]


def test_batch_describes_each_message_and_maps_each_image():
    fake = _PerCallVision([["猫"], ["狗"]])
    first = _msg("第一张 [图片]", ["u1"], user_id="user-a", name="甲")
    second = _msg("第二张 [图片]", ["u2"], user_id="user-b", name="乙")
    result = asyncio.run(describe_image_node(_state(first, second), fake))
    assert [m.content for m in result["messages"]] == [
        "第一张 [图片：猫]",
        "第二张 [图片：狗]",
    ]
    assert [m.additional_kwargs["user_id"] for m in result["messages"]] == [
        "user-a", "user-b",
    ]
    assert result["vision_desc"] == [
        ImageDescription("u1", "猫"),
        ImageDescription("u2", "狗"),
    ]


def test_ignores_previous_turn_images_when_target_count_is_set():
    fake = FakeVisionService(["旧图"])
    old = _msg("[图片]", ["old"])
    current = _msg("你好", [])
    state = make_state(
        messages=[old, current],
        content_kind="image",
        vision_target_count=1,
    )
    assert asyncio.run(describe_image_node(state, fake)) == {}
    assert fake.calls == 0


# --- 多模态模式（主 LLM 直接收图） ---

def test_multimodal_builds_content_array_and_vision_desc(monkeypatch):
    fake = FakeVisionService(["猫"])
    msg = _msg("看 [图片]", ["u1"])
    _patch_download(monkeypatch, {"u1": "data:image/jpeg;base64,AAA"})
    result = asyncio.run(
        describe_image_node(_state(msg), fake, llm_multimodal=True)
    )
    new_msg = result["messages"][0]
    assert isinstance(new_msg, HumanMessage)
    assert new_msg.id == msg.id
    assert new_msg.content == [
        {"type": "text", "text": "看 "},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAA"}},
    ]
    assert result["vision_desc"] == [ImageDescription("u1", "猫")]


def test_multimodal_without_vision_service(monkeypatch):
    msg = _msg("[图片]", ["u1"])
    _patch_download(monkeypatch, {"u1": "data:image/jpeg;base64,AAA"})
    result = asyncio.run(
        describe_image_node(_state(msg), None, llm_multimodal=True)
    )
    assert result["vision_desc"] == []
    assert result["messages"][0].content == [
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAA"}},
    ]


def test_auto_reply_multimodal_skips_vision_desc(monkeypatch):
    fake = FakeVisionService(["猫"])
    msg = _msg("看 [图片]", ["u1"])
    _patch_download(monkeypatch, {"u1": "data:image/jpeg;base64,AAA"})
    result = asyncio.run(
        describe_image_node(
            _state(msg, auto_reply=True), fake, llm_multimodal=True
        )
    )
    assert result["vision_desc"] == []
    assert fake.calls == 0
    assert result["messages"][0].content == [
        {"type": "text", "text": "看 "},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAA"}},
    ]


def test_multimodal_download_failed_but_vision_desc_indexes(monkeypatch):
    """图片下载全失败 → 占位符保留（LLM 看不到图），但逐图描述仍返回。"""
    fake = FakeVisionService(["猫"])
    msg = _msg("看 [图片]", ["u1"])
    _patch_download(monkeypatch, {"u1": ""})
    result = asyncio.run(
        describe_image_node(_state(msg), fake, llm_multimodal=True)
    )
    assert "messages" not in result
    assert result["vision_desc"] == [ImageDescription("u1", "猫")]


def test_multimodal_all_failed_clears_stale_vision_desc(monkeypatch):
    fake = FakeVisionService(["", ""])
    msg = _msg("看 [图片]", ["u1"])
    _patch_download(monkeypatch, {"u1": ""})
    assert asyncio.run(
        describe_image_node(_state(msg), fake, llm_multimodal=True)
    ) == {"vision_desc": []}


def test_multimodal_batch_processes_each_message(monkeypatch):
    fake = _PerCallVision([["猫"], ["狗"]])
    first = _msg("第一张 [图片]", ["u1"], user_id="user-a", name="甲")
    second = _msg("第二张 [图片]", ["u2"], user_id="user-b", name="乙")
    _patch_download(monkeypatch, {
        "u1": "data:image/jpeg;base64,AAA",
        "u2": "data:image/jpeg;base64,BBB",
    })
    result = asyncio.run(
        describe_image_node(_state(first, second), fake, llm_multimodal=True)
    )
    assert [m.additional_kwargs["user_id"] for m in result["messages"]] == [
        "user-a", "user-b",
    ]
    assert result["messages"][0].content == [
        {"type": "text", "text": "第一张 "},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAA"}},
    ]
    assert result["messages"][1].content == [
        {"type": "text", "text": "第二张 "},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,BBB"}},
    ]
    assert result["vision_desc"] == [
        ImageDescription("u1", "猫"),
        ImageDescription("u2", "狗"),
    ]


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
