"""index_turn_node：回复轮/群聊非@文本入库 + 多模态图片轮降级处理。"""

import asyncio

from bot.core.nodes import index_turn_node
from tests.fakes import StubRagService, make_state


def _run(state, rag):
    asyncio.run(index_turn_node(state, rag))
    return rag.last_indexed


def _img_state(**overrides):
    state = make_state(content_kind="image", clean_text="")
    state.update(overrides)
    return state


def test_replied_turn_indexes_user_and_bot():
    indexed = _run(make_state(reply_text="回复内容"), StubRagService())
    assert indexed["user_message"] == "你好"
    assert indexed["bot_reply"] == "回复内容"


def test_non_replied_group_text_indexes_user_only():
    indexed = _run(make_state(content_kind="text", clean_text="群聊普通发言"), StubRagService())
    assert indexed["user_message"] == "群聊普通发言"
    assert indexed["bot_reply"] == ""


def test_image_with_vision_desc_appends_description():
    indexed = _run(_img_state(vision_desc="一只橘猫", reply_text="图里是猫"), StubRagService())
    assert indexed["user_message"] == "[图片：一只橘猫]"
    assert indexed["bot_reply"] == "图里是猫"


def test_image_text_with_desc_appends_to_text():
    indexed = _run(
        _img_state(clean_text="帮我看看这张图", vision_desc="一只橘猫", reply_text="是猫"),
        StubRagService(),
    )
    assert indexed["user_message"] == "帮我看看这张图 [图片：一只橘猫]"


def test_pure_image_no_desc_no_reply_not_indexed():
    rag = StubRagService()
    _run(_img_state(), rag)
    assert rag.last_indexed is None


def test_pure_image_no_desc_but_reply_indexes_reply_only():
    """多模态 (1,0) 模式：纯图片无 vision_desc，但 bot 回复（含主 LLM 理解）仍入库。"""
    rag = StubRagService()
    indexed = _run(_img_state(reply_text="图里是一只橘猫在晒太阳"), rag)
    assert indexed["user_message"] == ""
    assert indexed["bot_reply"] == "图里是一只橘猫在晒太阳"
