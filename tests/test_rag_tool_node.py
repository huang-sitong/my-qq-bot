import asyncio

from langchain_core.messages import AIMessage, ToolMessage

from bot.core.nodes.tool_node import rag_tool_node
from tests.fakes import StubRagService, make_state

TOOL_CALL_MSG = AIMessage(content="", tool_calls=[
    {"name": "search_chat_history", "args": {"query": "之前聊了什么"},
     "id": "call_1", "type": "tool_call"},
])

SAMPLE = [
    {"thread_id": "test:thread", "user_id": "u1", "user_name": "张三",
     "content": "之前聊了 RAG", "role": "user", "timestamp": 1753910400,
     "score": 0.8},
]


def test_rag_tool_node_executes_tool_call():
    rag = StubRagService(search_results=SAMPLE)
    state = make_state(messages=[TOOL_CALL_MSG])
    result = asyncio.run(rag_tool_node(state, rag_service=rag))

    msgs = result["messages"]
    assert isinstance(msgs[0], ToolMessage)
    assert msgs[0].tool_call_id == "call_1"
    assert "之前聊了 RAG" in msgs[0].content
    assert rag.last_thread_id == "test:thread"


def test_rag_tool_node_noop_without_tool_calls():
    state = make_state(messages=[AIMessage(content="普通回复")])
    result = asyncio.run(rag_tool_node(state, rag_service=StubRagService()))
    assert result == {}


def test_rag_tool_node_degrades_on_search_error():
    rag = StubRagService(raise_on_search=True)
    state = make_state(messages=[TOOL_CALL_MSG])
    result = asyncio.run(rag_tool_node(state, rag_service=rag))
    assert result["messages"][0].content == "检索历史消息失败。"
