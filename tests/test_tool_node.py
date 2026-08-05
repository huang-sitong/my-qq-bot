import asyncio

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime

from bot.core.tools import build_tools
from tests.fakes import StubMemoryStore, StubRagService

RAG_CALL = AIMessage(content="", tool_calls=[
    {"name": "search_chat_history", "args": {"query": "之前聊了什么"},
     "id": "call_1", "type": "tool_call"},
])
RAG_CALL_USER = AIMessage(content="", tool_calls=[
    {"name": "search_chat_history",
     "args": {"query": "", "user_name": "张三", "hours": 24},
     "id": "call_5", "type": "tool_call"},
])
TIME_CALL = AIMessage(content="", tool_calls=[
    {"name": "search_chat_history",
     "args": {"query": "", "start_time": "2026-07-01", "end_time": "2026-08-01T23:59:59"},
     "id": "call_6", "type": "tool_call"},
])
RECALL_CALL = AIMessage(content="", tool_calls=[
    {"name": "recall_user_memory", "args": {"keyword": "食物"},
     "id": "call_2", "type": "tool_call"},
])
REMEMBER_CALL = AIMessage(content="", tool_calls=[
    {"name": "remember_user_memory", "args": {"key": "喜欢的食物", "value": "火锅"},
     "id": "call_3", "type": "tool_call"},
])
UNKNOWN_CALL = AIMessage(content="", tool_calls=[
    {"name": "no_such_tool", "args": {}, "id": "call_4", "type": "tool_call"},
])

SAMPLE = [
    {"thread_id": "test:thread", "sender_id": "u1", "sender_name": "张三",
     "receiver_id": "bot1", "receiver_name": "小助手",
     "content": "之前聊了 RAG", "timestamp": "2026-07-30 10:00:00",
     "score": 0.8},
]

DEFAULT_STATE = {"thread_id": "test:thread", "user_id": "u1"}


def _node(*, rag=None, store=None):
    return ToolNode(build_tools(rag_service=rag, memory_store=store))


def _invoke(node, state):
    """直接驱动 ToolNode（单元测试）。

    langgraph 1.2.2 起，ToolNode.ainvoke 需要注入 Pregel Runtime（编译图内自动注入，
    直接调用时缺省会抛 ValueError「Missing required config key 'N/A' for 'tools'」）。
    Runtime 为 langgraph.runtime 公共类，默认参数即可满足本测试场景。
    """
    return asyncio.run(node.ainvoke(state, runtime=Runtime()))


def test_executes_search_chat_history_query_mode():
    rag = StubRagService(search_results=SAMPLE)
    result = _invoke(_node(rag=rag, store=StubMemoryStore()),
                     {"messages": [RAG_CALL], **DEFAULT_STATE})
    assert isinstance(result["messages"][0], ToolMessage)
    assert "之前聊了 RAG" in result["messages"][0].content
    assert rag.last_query == "之前聊了什么"
    assert rag.last_thread_id == "test:thread"


def test_executes_search_by_user_sql_mode():
    rag = StubRagService(search_results=SAMPLE)
    result = _invoke(_node(rag=rag, store=StubMemoryStore()),
                     {"messages": [RAG_CALL_USER], **DEFAULT_STATE})
    assert "之前聊了 RAG" in result["messages"][0].content
    assert rag.last_person == "张三"
    assert rag.last_thread_id == "test:thread"


def test_executes_search_by_time_window():
    rag = StubRagService(search_results=SAMPLE)
    result = _invoke(_node(rag=rag, store=StubMemoryStore()),
                     {"messages": [TIME_CALL], **DEFAULT_STATE})
    assert "之前聊了 RAG" in result["messages"][0].content
    assert rag.last_start_time == "2026-07-01 00:00:00"
    assert rag.last_end_time == "2026-08-01 23:59:59"


def test_executes_recall_to_memory():
    store = StubMemoryStore()
    store.store_memory("u1", "喜欢的食物", "火锅")
    result = _invoke(_node(store=store), {"messages": [RECALL_CALL], **DEFAULT_STATE})
    assert "火锅" in result["messages"][0].content


def test_executes_remember_to_memory():
    store = StubMemoryStore()
    result = _invoke(_node(store=store), {"messages": [REMEMBER_CALL], **DEFAULT_STATE})
    assert "已记住" in result["messages"][0].content
    assert store.load_memories("u1") == [{"key": "喜欢的食物", "value": "火锅"}]


def test_unknown_tool_returns_error_message():
    result = _invoke(_node(rag=StubRagService(), store=StubMemoryStore()),
                     {"messages": [UNKNOWN_CALL], **DEFAULT_STATE})
    msg = result["messages"][0]
    assert isinstance(msg, ToolMessage)
    assert msg.status == "error"
    assert "no_such_tool" in msg.content


def test_noop_without_tool_calls():
    state = {"messages": [AIMessage(content="普通回复")], **DEFAULT_STATE}
    result = _invoke(_node(rag=StubRagService(), store=StubMemoryStore()), state)
    assert result["messages"] == []


def test_degrades_on_tool_error():
    rag = StubRagService(raise_on_search=True)
    result = _invoke(_node(rag=rag), {"messages": [RAG_CALL], **DEFAULT_STATE})
    assert result["messages"][0].content == "工具执行失败。"
