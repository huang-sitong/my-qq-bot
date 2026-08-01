"""MessageHandler._index_turn：RAG 索引前的消息清洗（纯媒体跳过、at 剥离）。"""

import asyncio

from bot.handler import MessageHandler
from tests.fakes import StubRagService


def _handler(rag: StubRagService) -> MessageHandler:
    return MessageHandler(None, None, "persona", None, rag_service=rag)


def test_index_turn_skips_media_only():
    rag = StubRagService()
    asyncio.run(_handler(rag)._index_turn("t", "u", "name", '<img src="x"/>', "收到"))
    assert rag.last_indexed is None


def test_index_turn_strips_at_mention():
    rag = StubRagService()
    asyncio.run(_handler(rag)._index_turn("t", "u", "name", '<at id="bot" name="Bot"/> 你好', "收到"))
    assert rag.last_indexed is not None
    assert rag.last_indexed["user_message"] == "你好"
    assert rag.last_indexed["bot_reply"] == "收到"


def test_index_turn_keeps_text_beside_image():
    rag = StubRagService()
    asyncio.run(_handler(rag)._index_turn("t", "u", "name", '今天真开心<img src="x"/>', "收到"))
    assert rag.last_indexed["user_message"] == "今天真开心"


def test_index_turn_unescapes_entities():
    rag = StubRagService()
    asyncio.run(_handler(rag)._index_turn("t", "u", "name", "A &amp; B", "收到"))
    assert rag.last_indexed["user_message"] == "A & B"
