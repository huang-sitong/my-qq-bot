"""集成测试：真实 MemoryStore（langgraph AsyncSqliteStore 后端）经 ToolNode 执行记忆工具。

使用真实 SQLite 数据库（tmp_path），覆盖 MemoryStore 惰性初始化 + 纯 async 读写路径
（官方 AsyncSqliteStore，方法全 async，工具层直接 await）。若惰性建连/异步路径出错，
会被工具包装层降级为「工具执行失败。」—— 本测试用于兜住这一类回归。
"""

import asyncio

from langchain_core.messages import AIMessage
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime

from bot.core.memory import MemoryStore
from bot.core.tools import build_tools
from tests.fakes import make_state

REMEMBER_CALL = AIMessage(content="", tool_calls=[
    {"name": "remember_user_memory", "args": {"key": "名字", "value": "张三"},
     "id": "call_r", "type": "tool_call"},
])
RECALL_CALL = AIMessage(content="", tool_calls=[
    {"name": "recall_user_memory", "args": {"keyword": "名字"},
     "id": "call_c", "type": "tool_call"},
])


def _node(store):
    return ToolNode(build_tools(memory_store=store))


def test_memory_tools_work_with_real_store(tmp_path):
    async def run():
        # 注意：AsyncSqliteStore 在构造时捕获当前事件循环，MemoryStore 惰性初始化
        # 后必须全程在同一个事件循环使用（与真实 bot 常驻单 loop 一致）。
        store = MemoryStore(db_dir=str(tmp_path))
        node = _node(store)
        runtime = Runtime()

        # 写入：真实 MemoryStore 经 ToolNode 执行 remember（惰性初始化 + async 路径）
        remember_state = make_state(messages=[REMEMBER_CALL], user_id="u1")
        remember_result = await node.ainvoke(remember_state, runtime=runtime)
        remember_content = remember_result["messages"][0].content
        assert "已记住" in remember_content
        assert "工具执行失败。" not in remember_content

        # 检索：真实 MemoryStore 经 ToolNode 执行 recall，应命中刚写入的记忆
        recall_state = make_state(messages=[RECALL_CALL], user_id="u1")
        recall_result = await node.ainvoke(recall_state, runtime=runtime)
        recall_content = recall_result["messages"][0].content
        assert "张三" in recall_content
        assert "工具执行失败。" not in recall_content

        await store.close()

    asyncio.run(run())
