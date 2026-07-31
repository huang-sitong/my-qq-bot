import asyncio

from langchain_core.messages import AIMessage, HumanMessage

from bot.core.graph import create_graph
from common import BotConfig
from tests.fakes import ScriptedLLM, StubRagService

TOOL_CALLS = [
    {"name": "search_chat_history", "args": {"query": "之前聊了什么"}, "id": "call_1", "type": "tool_call"},
]

SAMPLE = [
    {"thread_id": "test:thread", "user_id": "u1", "user_name": "张三",
     "content": "上次我们决定用 qwen3-embedding", "role": "user",
     "timestamp": 1753910400, "score": 0.85},
]


def _initial_state() -> dict:
    # channel_type=1 (DIRECT) → detect_intent 直接置 should_respond=True，router 不消耗脚本消息
    return {
        "new_message": HumanMessage(content="还记得我们聊过 RAG 吗？"),
        "session_id": "test:session",
        "thread_id": "test:thread",
        "persona": "你是{bot_name}",
        "user_memories": "",
        "reply_text": "",
        "should_respond": False,
        "bot_name": "测试机器人",
        "bot_id": "bot1",
        "channel_type": 1,
        "raw_content": "还记得我们聊过 RAG 吗？",
        "user_name": "张三",
        "rag_tool_rounds": 0,
    }


def test_graph_loops_tool_call_then_answers(tmp_path):
    rag = StubRagService(search_results=SAMPLE)
    llm = ScriptedLLM([
        # 第一次 call_llm：请求调用工具
        AIMessage(content="", tool_calls=TOOL_CALLS),
        # 第二次 call_llm（回环后）：给出最终回复
        AIMessage(content="我们上次决定用 qwen3-embedding 做嵌入"),
    ])
    graph, _ = asyncio.run(
        create_graph(llm, BotConfig(rag_enabled=True), db_dir=str(tmp_path), rag_service=rag)
    )

    result = asyncio.run(graph.ainvoke(_initial_state(), {"configurable": {"thread_id": "test:thread"}}))

    assert result["reply_text"] == "我们上次决定用 qwen3-embedding 做嵌入"
    # 循环确实发生：state 中应包含 ToolMessage
    assert any(type(m).__name__ == "ToolMessage" for m in result["messages"])
