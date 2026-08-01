"""index_turn_node：RAG 索引图节点（无回复也索引用户消息、纯媒体跳过、at 剥离、unescape）。"""

import asyncio

from bot.core.nodes import index_turn_node
from tests.fakes import StubRagService, make_state


def _run(rag, **state):
    asyncio.run(index_turn_node(make_state(**state), rag))


def test_index_turn_noop_when_rag_disabled():
    _run(None, raw_content="你好", reply_text="收到")  # 不抛异常即可


def test_index_turn_indexes_user_message_without_reply():
    rag = StubRagService()
    _run(rag, raw_content="你好", reply_text="")
    assert rag.last_indexed is not None
    assert rag.last_indexed["user_message"] == "你好"
    assert rag.last_indexed["bot_reply"] == ""  # service 层过滤 → 只索引 1 条


def test_index_turn_skips_media_only():
    rag = StubRagService()
    _run(rag, raw_content='<img src="x"/>', reply_text="收到", content_kind="image")
    assert rag.last_indexed is None


def test_index_turn_strips_at_mention():
    rag = StubRagService()
    _run(rag, raw_content='<at id="bot" name="Bot"/> 你好', reply_text="收到")
    assert rag.last_indexed is not None
    assert rag.last_indexed["user_message"] == "你好"
    assert rag.last_indexed["bot_reply"] == "收到"


def test_index_turn_keeps_text_beside_image():
    rag = StubRagService()
    _run(rag, raw_content='今天真开心<img src="x"/>', reply_text="收到")
    assert rag.last_indexed["user_message"] == "今天真开心"


def test_index_turn_unescapes_entities():
    rag = StubRagService()
    _run(rag, raw_content="A &amp; B", reply_text="收到")
    assert rag.last_indexed["user_message"] == "A & B"


def test_index_turn_appends_vision_desc_for_image():
    rag = StubRagService()
    _run(rag, raw_content='<img src="https://x/1.jpg"/>', reply_text="收到",
         content_kind="image", vision_desc="一只猫")
    assert rag.last_indexed is not None
    assert rag.last_indexed["user_message"] == "[图片：一只猫]"


def test_index_turn_image_without_vision_skips():
    rag = StubRagService()
    _run(rag, raw_content='<img src="https://x/1.jpg"/>', reply_text="收到",
         content_kind="image")
    assert rag.last_indexed is None


def test_index_turn_text_ignores_stale_vision_desc():
    rag = StubRagService()
    # text 轮残留上一张图的 vision_desc → content_kind=="text" 过滤，不追加
    _run(rag, raw_content="晚上吃什么", reply_text="去吃火锅",
         content_kind="text", vision_desc="一只猫")
    assert rag.last_indexed is not None
    assert rag.last_indexed["user_message"] == "晚上吃什么"
