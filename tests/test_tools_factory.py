"""build_tools 工具统一层测试：组装、schema 排除注入参数。"""

from bot.core.tools import build_tools
from tests.fakes import StubMemoryStore, StubRagService


def _names(tools):
    return {t.name for t in tools}


def test_rag_tool_present_when_rag_enabled():
    tools = build_tools(rag_service=StubRagService(), memory_store=StubMemoryStore())
    assert "search_chat_history" in _names(tools)


def test_no_rag_tool_when_disabled():
    tools = build_tools(rag_service=StubRagService(enabled=False), memory_store=None)
    assert "search_chat_history" not in _names(tools)


def test_memory_tools_present_when_store_injected():
    tools = build_tools(rag_service=None, memory_store=StubMemoryStore())
    assert {"remember_user_memory", "recall_user_memory"} <= _names(tools)


def test_mcp_tools_appended():
    class FakeMcpTool:
        name = "web_search"

    tools = build_tools(rag_service=None, memory_store=None, mcp_tools=[FakeMcpTool()])
    assert "web_search" in _names(tools)


def test_llm_schema_excludes_injected_args():
    tools = build_tools(rag_service=StubRagService(), memory_store=StubMemoryStore())
    by_name = {t.name: t for t in tools}

    search = by_name["search_chat_history"]
    props = search.tool_call_schema.model_json_schema()["properties"]
    assert "thread_id" not in props
    assert {"query", "user_name", "content_keyword", "start_time", "end_time", "hours"} <= set(props)

    recall = by_name["recall_user_memory"]
    recall_props = recall.tool_call_schema.model_json_schema()["properties"]
    assert "user_id" not in recall_props
    assert "keyword" in recall_props


def test_llm_schema_has_param_descriptions():
    """每个参数必须有 description —— 回归手写 TOOL_SCHEMA 删除后的 schema 退化。"""
    tools = build_tools(rag_service=StubRagService(), memory_store=StubMemoryStore())
    by_name = {t.name: t for t in tools}

    search = by_name["search_chat_history"]
    search_props = search.tool_call_schema.model_json_schema()["properties"]
    assert "最近 N 小时" in search_props["hours"]["description"]
    assert "中文表述" in search_props["query"]["description"]

    remember = by_name["remember_user_memory"]
    remember_props = remember.tool_call_schema.model_json_schema()["properties"]
    assert "语义标签" in remember_props["key"]["description"]

    recall = by_name["recall_user_memory"]
    recall_props = recall.tool_call_schema.model_json_schema()["properties"]
    assert "模糊匹配" in recall_props["keyword"]["description"]
