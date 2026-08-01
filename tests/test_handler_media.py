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
    _run(rag, raw_content='<img src="x"/>', reply_text="收到")
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
