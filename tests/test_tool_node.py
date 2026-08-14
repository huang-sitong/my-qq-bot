import asyncio

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool as make_tool
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime

from bot.core.graph import _tool_error_message
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
RECALL_CALL_BY_NAME = AIMessage(content="", tool_calls=[
    {"name": "recall_user_memory", "args": {"keyword": "食物", "user_name": "甲"},
     "id": "call_name", "type": "tool_call"},
])
REMEMBER_CALL_BY_ID = AIMessage(content="", tool_calls=[
    {"name": "remember_user_memory",
     "args": {"key": "喜欢的食物", "value": "火锅", "user_id": "u2"},
     "id": "call_id", "type": "tool_call"},
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

DEFAULT_STATE = {"thread_id": "test:thread"}

USER_MSG = HumanMessage(
    content="我的信息",
    name="张三",
    additional_kwargs={"user_id": "u1", "user_name": "张三"},
)
USER_MSG_A = HumanMessage(
    content="甲的消息",
    name="甲",
    additional_kwargs={"user_id": "user-a", "user_name": "甲"},
)
USER_MSG_B = HumanMessage(
    content="乙的消息",
    name="乙",
    additional_kwargs={"user_id": "user-b", "user_name": "乙"},
)


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
    assert rag.last_thread_id is None  # 属性检索跨全部群（取消群聊限制）


def test_executes_search_by_time_window():
    rag = StubRagService(search_results=SAMPLE)
    result = _invoke(_node(rag=rag, store=StubMemoryStore()),
                     {"messages": [TIME_CALL], **DEFAULT_STATE})
    assert "之前聊了 RAG" in result["messages"][0].content
    assert rag.last_start_time == "2026-07-01 00:00:00"
    assert rag.last_end_time == "2026-08-01 23:59:59"


def test_executes_recall_to_memory():
    store = StubMemoryStore()
    asyncio.run(store.store_memory("u1", "喜欢的食物", "火锅"))
    result = _invoke(
        _node(store=store),
        {"messages": [USER_MSG, RECALL_CALL], **DEFAULT_STATE},
    )
    assert "火锅" in result["messages"][0].content


def test_executes_remember_to_memory():
    store = StubMemoryStore()
    result = _invoke(
        _node(store=store),
        {"messages": [USER_MSG, REMEMBER_CALL], **DEFAULT_STATE},
    )
    assert "已记住" in result["messages"][0].content
    assert asyncio.run(store.load_memories("u1")) == [{"key": "喜欢的食物", "value": "火锅"}]


def test_memory_tool_resolves_user_name_from_batch_context():
    store = StubMemoryStore()
    asyncio.run(store.store_memory("user-a", "喜欢的食物", "火锅"))
    result = _invoke(
        _node(store=store),
        {
            "messages": [USER_MSG_A, USER_MSG_B, RECALL_CALL_BY_NAME],
            **DEFAULT_STATE,
        },
    )
    assert "火锅" in result["messages"][0].content


def test_memory_tool_accepts_explicit_user_id():
    store = StubMemoryStore()
    result = _invoke(
        _node(store=store),
        {"messages": [USER_MSG, REMEMBER_CALL_BY_ID], **DEFAULT_STATE},
    )
    assert "已记住" in result["messages"][0].content
    assert asyncio.run(store.load_memories("u2")) == [
        {"key": "喜欢的食物", "value": "火锅"}
    ]


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


def test_handle_tool_errors_degrades_on_raising_tool():
    """ToolNode handle_tool_errors 回调兜住工具抛出的任意异常（含 MCP 传输层失败）。

    内部工具已自带降级，但 MCP 外部工具（如 Tavily）的传输/会话异常会直接抛出
    tool.ainvoke，本测试用 `_tool_error_message`（graph.py 的同一回调）证明
    ToolNode 把这类异常转成「工具执行失败。」ToolMessage 而非中断整轮。
    """
    @make_tool
    def boom(x: int) -> str:
        """raise 一个含 URL 的异常，验证日志只记类名不记 repr。"""
        raise RuntimeError("secret https://mcp.tavily.com/mcp/?tavilyApiKey=LEAK")

    node = ToolNode([boom], handle_tool_errors=_tool_error_message)
    call = AIMessage(content="", tool_calls=[
        {"name": "boom", "args": {"x": 1}, "id": "call_boom", "type": "tool_call"},
    ])
    result = _invoke(node, {"messages": [call], **DEFAULT_STATE})
    msg = result["messages"][0]
    assert isinstance(msg, ToolMessage)
    assert msg.status == "error"
    assert msg.content == "工具执行失败。"
    assert "LEAK" not in msg.content


def test_handle_tool_errors_preserves_validation_feedback():
    """畸形参数（ToolInvocationError）返回逐字段校验信息供 LLM 自我纠正，而非降级占位文案。"""
    tools = build_tools(rag_service=StubRagService(), memory_store=StubMemoryStore())
    node = ToolNode(tools, handle_tool_errors=_tool_error_message)
    call = AIMessage(content="", tool_calls=[
        {"name": "search_chat_history", "args": {"query": "x", "hours": "notanint"},
         "id": "call_bad_hours", "type": "tool_call"},
    ])
    result = _invoke(node, {"messages": [call], **DEFAULT_STATE})
    msg = result["messages"][0]
    assert isinstance(msg, ToolMessage)
    assert msg.status == "error"
    assert msg.content != "工具执行失败。"
    assert "hours" in msg.content and "valid integer" in msg.content
