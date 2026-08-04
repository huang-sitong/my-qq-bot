import asyncio

from bot.core.tools import TOOL_SCHEMA, search_chat_history
from bot.core.tools.search_chat_history import _format_results
from tests.fakes import StubRagService

SAMPLE = [
    {
        "thread_id": "g", "sender_id": "u1", "sender_name": "张三",
        "receiver_id": "bot1", "receiver_name": "小助手",
        "content": "之前决定用 qwen3-embedding 做嵌入", "timestamp": 1753910400,
        "score": 0.9,
    },
]


def test_tool_schema_exposes_query_param():
    fn = TOOL_SCHEMA["function"]
    assert fn["name"] == "search_chat_history"
    props = fn["parameters"]["properties"]
    assert "query" in props
    assert "user_name" in props  # SQL 属性检索模式
    assert "content_keyword" in props
    assert "hours" in props


def test_format_results_renders_speaker_and_content():
    text = _format_results(SAMPLE)
    assert "张三" in text
    assert "之前决定用 qwen3-embedding 做嵌入" in text


def test_format_results_shows_receiver_with_arrow():
    text = _format_results(SAMPLE)
    assert "张三 → 小助手" in text


def test_format_results_empty():
    assert _format_results([]) == "没有找到相关的历史消息。"


def test_search_chat_history_returns_formatted_text():
    rag = StubRagService(search_results=SAMPLE)
    text = asyncio.run(search_chat_history("嵌入模型", rag, "test:thread"))
    assert "之前决定用 qwen3-embedding 做嵌入" in text
    assert rag.last_query == "嵌入模型"
    assert rag.last_thread_id == "test:thread"


def test_search_chat_history_sql_mode_with_user_name():
    rag = StubRagService(search_results=SAMPLE)
    text = asyncio.run(search_chat_history(
        "", rag, "test:thread", user_name="张三", hours=24))
    assert "之前决定用 qwen3-embedding 做嵌入" in text
    assert rag.last_person == "张三"
    assert rag.last_thread_id == "test:thread"


def test_search_chat_history_sql_mode_with_content_keyword():
    rag = StubRagService(search_results=SAMPLE)
    text = asyncio.run(search_chat_history(
        "", rag, "test:thread", content_keyword="qwen3"))
    assert rag.last_content_keyword == "qwen3"
    assert "之前决定用 qwen3-embedding 做嵌入" in text
