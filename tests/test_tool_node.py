import asyncio

from langchain_core.messages import AIMessage

from bot.core.nodes.tool_node import tool_node
from tests.fakes import StubMemoryStore, StubRagService, make_state

RAG_CALL = AIMessage(content="", tool_calls=[
    {"name": "search_chat_history", "args": {"query": "之前聊了什么"},
     "id": "call_1", "type": "tool_call"},
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
    {"thread_id": "test:thread", "user_id": "u1", "user_name": "张三",
     "content": "之前聊了 RAG", "role": "user", "timestamp": 1753910400,
     "score": 0.8},
]


def test_dispatches_search_chat_history_to_rag():
    rag = StubRagService(search_results=SAMPLE)
    state = make_state(messages=[RAG_CALL])
    result = asyncio.run(tool_node(state, rag_service=rag, memory_store=StubMemoryStore()))
    assert "之前聊了 RAG" in result["messages"][0].content
    assert rag.last_query == "之前聊了什么"
    assert rag.last_thread_id == "test:thread"


def test_dispatches_recall_to_memory():
    store = StubMemoryStore()
    store.store_memory("u1", "喜欢的食物", "火锅")
    state = make_state(messages=[RECALL_CALL], user_id="u1")
    result = asyncio.run(tool_node(state, memory_store=store))
    assert "火锅" in result["messages"][0].content


def test_dispatches_remember_to_memory():
    store = StubMemoryStore()
    state = make_state(messages=[REMEMBER_CALL], user_id="u1")
    result = asyncio.run(tool_node(state, memory_store=store))
    assert "已记住" in result["messages"][0].content
    assert store.load_memories("u1") == [{"key": "喜欢的食物", "value": "火锅"}]


def test_unknown_tool_returns_placeholder():
    state = make_state(messages=[UNKNOWN_CALL])
    result = asyncio.run(tool_node(state))
    assert result["messages"][0].content == "未知工具：no_such_tool"


def test_noop_without_tool_calls():
    state = make_state(messages=[AIMessage(content="普通回复")])
    result = asyncio.run(tool_node(state))
    assert result == {}


def test_degrades_on_tool_error():
    rag = StubRagService(raise_on_search=True)
    state = make_state(messages=[RAG_CALL])
    result = asyncio.run(tool_node(state, rag_service=rag))
    assert result["messages"][0].content == "工具执行失败。"
