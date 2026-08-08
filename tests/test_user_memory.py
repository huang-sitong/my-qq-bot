import asyncio

from bot.core.tools import recall_user_memory, remember_user_memory
from bot.core.tools.user_memory import _format_memories
from tests.fakes import StubMemoryStore


def test_remember_stores_by_user():
    store = StubMemoryStore()
    text = asyncio.run(remember_user_memory("名字", "张三", store, "u1"))
    assert text == "已记住：名字 = 张三"
    assert asyncio.run(store.load_memories("u1")) == [{"key": "名字", "value": "张三"}]
    assert asyncio.run(store.load_memories("u2")) == []


def test_recall_returns_all_when_keyword_empty():
    store = StubMemoryStore()
    asyncio.run(store.store_memory("u1", "名字", "张三"))
    asyncio.run(store.store_memory("u1", "喜欢的食物", "火锅"))
    text = asyncio.run(recall_user_memory("", store, "u1"))
    assert "名字：张三" in text
    assert "喜欢的食物：火锅" in text


def test_recall_filters_by_keyword_substring():
    store = StubMemoryStore()
    asyncio.run(store.store_memory("u1", "喜欢的食物", "火锅"))
    asyncio.run(store.store_memory("u1", "城市", "上海"))
    text = asyncio.run(recall_user_memory("食物", store, "u1"))
    assert "火锅" in text
    assert "上海" not in text


def test_recall_empty_result():
    store = StubMemoryStore()
    text = asyncio.run(recall_user_memory("", store, "u1"))
    assert text == "没有找到相关记忆。"


def test_format_memories_renders_lines():
    assert _format_memories([{"key": "名字", "value": "张三"}]) == "- 名字：张三"
    assert _format_memories([]) == "没有找到相关记忆。"
