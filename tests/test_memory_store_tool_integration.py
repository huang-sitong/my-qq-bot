"""集成测试：真实 MemoryStore 经 tool_node 执行记忆工具。

使用真实 SQLite 数据库（tmp_path），覆盖 asyncio.to_thread 线程池调用路径。
若 MemoryStore 未做线程安全处理（check_same_thread=True + 无锁），每次读写
都会在 to_thread 线程抛 sqlite3.ProgrammingError，被 tool_node 降级为
「工具执行失败。」—— 本测试用于兜住这一类回归。
"""

import asyncio

from langchain_core.messages import AIMessage

from bot.core.memory import MemoryStore
from bot.core.nodes.tool_node import tool_node
from tests.fakes import make_state

REMEMBER_CALL = AIMessage(content="", tool_calls=[
    {"name": "remember_user_memory", "args": {"key": "名字", "value": "张三"},
     "id": "call_r", "type": "tool_call"},
])
RECALL_CALL = AIMessage(content="", tool_calls=[
    {"name": "recall_user_memory", "args": {"keyword": "名字"},
     "id": "call_c", "type": "tool_call"},
])


def test_memory_tools_work_with_real_store(tmp_path):
    store = MemoryStore(db_dir=str(tmp_path))

    # 写入：真实 MemoryStore 经 tool_node 执行 remember（to_thread 线程池路径）
    remember_state = make_state(messages=[REMEMBER_CALL], user_id="u1")
    remember_result = asyncio.run(tool_node(remember_state, memory_store=store))
    remember_content = remember_result["messages"][0].content
    assert "已记住" in remember_content
    assert "工具执行失败。" not in remember_content

    # 检索：真实 MemoryStore 经 tool_node 执行 recall，应命中刚写入的记忆
    recall_state = make_state(messages=[RECALL_CALL], user_id="u1")
    recall_result = asyncio.run(tool_node(recall_state, memory_store=store))
    recall_content = recall_result["messages"][0].content
    assert "张三" in recall_content
    assert "工具执行失败。" not in recall_content
