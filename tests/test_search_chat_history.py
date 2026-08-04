import asyncio

from bot.core.tools import TOOL_SCHEMA, search_chat_history
from bot.core.tools.search_chat_history import _format_results
from tests.fakes import StubRagService

SAMPLE = [
    {
        "thread_id": "g", "sender_id": "u1", "sender_name": "张三",
        "receiver_id": "bot1", "receiver_name": "小助手",
        "content": "之前决定用 qwen3-embedding 做嵌入", "timestamp": "2026-07-30 12:00:00",
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
    assert "start_time" in props  # ISO 时间窗口
    assert "end_time" in props
    assert "hours" not in props  # 相对窗口已移除，只保留绝对时间窗


def test_format_results_renders_speaker_and_content():
    text = _format_results(SAMPLE)
    assert "张三" in text
    assert "之前决定用 qwen3-embedding 做嵌入" in text


def test_format_results_shows_receiver_with_arrow():
    text = _format_results(SAMPLE)
    assert "张三 → 小助手" in text


def test_format_results_shows_iso_time_to_minute():
    text = _format_results(SAMPLE)
    assert "2026-07-30 12:00" in text  # ISO 存储，展示截到分钟


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
        "", rag, "test:thread", user_name="张三"))
    assert "之前决定用 qwen3-embedding 做嵌入" in text
    assert rag.last_person == "张三"
    assert rag.last_thread_id == "test:thread"


def test_search_chat_history_sql_mode_with_content_keyword():
    rag = StubRagService(search_results=SAMPLE)
    text = asyncio.run(search_chat_history(
        "", rag, "test:thread", content_keyword="qwen3"))
    assert rag.last_content_keyword == "qwen3"
    assert "之前决定用 qwen3-embedding 做嵌入" in text


def test_search_chat_history_time_window_normalized():
    rag = StubRagService(search_results=SAMPLE)
    text = asyncio.run(search_chat_history(
        "", rag, "test:thread",
        start_time="2026-07-01",  # 日期缺省 → 当日 00:00:00
        end_time="2026-08-01T23:59:59",  # T 分隔 → 空格分隔
    ))
    assert "之前决定用 qwen3-embedding 做嵌入" in text
    assert rag.last_start_time == "2026-07-01 00:00:00"
    assert rag.last_end_time == "2026-08-01 23:59:59"


def test_search_chat_history_invalid_time_returns_error():
    rag = StubRagService()
    text = asyncio.run(search_chat_history("", rag, "test:thread", start_time="昨天"))
    assert "时间参数格式无效" in text
    assert rag.last_person is None  # 未下钻到检索
