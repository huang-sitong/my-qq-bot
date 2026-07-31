import asyncio

from bot.core.tools import TOOL_SCHEMA, search_chat_history
from bot.core.tools.search_chat_history import _format_results
from tests.fakes import StubRagService

SAMPLE = [
    {
        "thread_id": "g", "user_id": "u1", "user_name": "张三",
        "content": "之前决定用 qwen3-embedding 做嵌入", "role": "user",
        "timestamp": 1753910400, "score": 0.9,
    },
]


def test_tool_schema_exposes_query_param():
    fn = TOOL_SCHEMA["function"]
    assert fn["name"] == "search_chat_history"
    assert "query" in fn["parameters"]["properties"]


def test_format_results_renders_speaker_and_content():
    text = _format_results(SAMPLE)
    assert "张三" in text
    assert "之前决定用 qwen3-embedding 做嵌入" in text


def test_format_results_empty():
    assert _format_results([]) == "没有找到相关的历史消息。"


def test_search_chat_history_returns_formatted_text():
    rag = StubRagService(search_results=SAMPLE)
    text = asyncio.run(search_chat_history("嵌入模型", rag, "test:thread"))
    assert "之前决定用 qwen3-embedding 做嵌入" in text
    assert rag.last_query == "嵌入模型"
    assert rag.last_thread_id == "test:thread"
