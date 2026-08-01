import asyncio

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from bot.core.graph import create_graph
from common import BotConfig
from tests.fakes import FakeVisionService, ScriptedLLM, StubMemoryStore, StubRagService

TOOL_CALLS = [
    {"name": "search_chat_history", "args": {"query": "之前聊了什么"}, "id": "call_1", "type": "tool_call"},
]

SAMPLE = [
    {"thread_id": "test:thread", "user_id": "u1", "user_name": "张三",
     "content": "上次我们决定用 qwen3-embedding", "role": "user",
     "timestamp": 1753910400, "score": 0.85},
]


def _initial_state() -> dict:
    # channel_type=1 (DIRECT) → detect_intent 置 should_respond=True → call_llm
    return {
        "new_message": HumanMessage(content="还记得我们聊过 RAG 吗？"),
        "session_id": "test:session",
        "thread_id": "test:thread",
        "persona": "你是{bot_name}",
        "reply_text": "",
        "should_respond": False,
        "bot_name": "测试机器人",
        "bot_id": "bot1",
        "channel_type": 1,
        "raw_content": "还记得我们聊过 RAG 吗？",
        "user_name": "张三",
        "user_id": "u1",
        "tool_rounds": 0,
        "content_kind": "text",
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
    # 循环确实发生：state 中应包含 ToolMessage，且 stub 检索结果流入其 content
    tool_msgs = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert tool_msgs, "expected a ToolMessage from the tool loop"
    assert "上次我们决定用 qwen3-embedding" in tool_msgs[0].content


def test_graph_memory_tool_roundtrip(tmp_path):
    store = StubMemoryStore()
    store.store_memory("u1", "名字", "张三")
    llm = ScriptedLLM([
        # 第一次 call_llm：请求调用 recall 工具
        AIMessage(content="", tool_calls=[
            {"name": "recall_user_memory", "args": {"keyword": "名字"},
             "id": "call_m", "type": "tool_call"},
        ]),
        # 第二次 call_llm（回环后）：给出最终回复
        AIMessage(content="你之前说过你叫张三"),
    ])
    graph, _ = asyncio.run(
        create_graph(llm, BotConfig(rag_enabled=False), db_dir=str(tmp_path), memory_store=store)
    )

    result = asyncio.run(graph.ainvoke(_initial_state(), {"configurable": {"thread_id": "test:thread"}}))

    assert result["reply_text"] == "你之前说过你叫张三"
    tool_msgs = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert tool_msgs
    assert "张三" in tool_msgs[0].content


# ----------------------------------------------------------------------
# 确定性路由（router 已摘除）：群聊非@ text 入上下文+单条索引；媒体直接 END
# ----------------------------------------------------------------------

def test_group_non_mention_text_indexes_without_reply(tmp_path):
    rag = StubRagService()
    # 非回复路径不触发任何 LLM：call_llm 不执行，summarize 低于阈值 no-op
    llm = ScriptedLLM([])
    graph, _ = asyncio.run(
        create_graph(llm, BotConfig(rag_enabled=True), db_dir=str(tmp_path), rag_service=rag)
    )
    state = {
        **_initial_state(),
        "channel_type": 0,  # 群聊
        "raw_content": "晚上吃什么",
        "llm_text": "晚上吃什么",
    }
    result = asyncio.run(graph.ainvoke(state, {"configurable": {"thread_id": "test:thread"}}))

    assert result["reply_text"] == ""  # 不回复
    assert rag.last_indexed is not None  # 但索引了用户消息
    assert rag.last_indexed["user_message"] == "晚上吃什么"
    assert rag.last_indexed["bot_reply"] == ""  # 空回复 → 只索引 1 条


def test_group_non_mention_image_ends_without_index(tmp_path):
    rag = StubRagService()
    graph, _ = asyncio.run(
        create_graph(ScriptedLLM([]), BotConfig(rag_enabled=True), db_dir=str(tmp_path), rag_service=rag)
    )
    state = {
        **_initial_state(),
        "channel_type": 0,  # 群聊
        "content_kind": "image",
        "raw_content": '<img src="x"/>',
    }
    result = asyncio.run(graph.ainvoke(state, {"configurable": {"thread_id": "test:thread"}}))

    assert result["reply_text"] == ""
    assert rag.last_indexed is None  # 不索引
    assert result["messages"] == []  # 不入上下文


def test_private_file_ends_without_reply(tmp_path):
    rag = StubRagService()
    graph, _ = asyncio.run(
        create_graph(ScriptedLLM([]), BotConfig(rag_enabled=True), db_dir=str(tmp_path), rag_service=rag)
    )
    state = {
        **_initial_state(),
        "content_kind": "file",  # 私聊 + 文件 → 媒体门盖过 DIRECT
        "raw_content": '<file src="x"/>',
    }
    result = asyncio.run(graph.ainvoke(state, {"configurable": {"thread_id": "test:thread"}}))

    assert result["reply_text"] == ""
    assert rag.last_indexed is None
    assert result["messages"] == []


def test_graph_image_reply_includes_vision_description(tmp_path):
    rag = StubRagService()
    vision = FakeVisionService(["一只猫坐在窗台上"])
    llm = ScriptedLLM([AIMessage(content="好可爱的猫！")])
    graph, _ = asyncio.run(
        create_graph(
            llm, BotConfig(rag_enabled=True), db_dir=str(tmp_path),
            rag_service=rag, vision_service=vision,
        )
    )
    state = {
        **_initial_state(),
        "content_kind": "image",
        "raw_content": '<img src="https://x/1.jpg"/>',
        "llm_text": "[图片]",
        "image_srcs": ["https://x/1.jpg"],
    }
    result = asyncio.run(graph.ainvoke(state, {"configurable": {"thread_id": "test:thread"}}))

    assert result["reply_text"] == "好可爱的猫！"
    humans = [m for m in result["messages"] if isinstance(m, HumanMessage)]
    assert humans and humans[0].content == "[图片：一只猫坐在窗台上]"
    assert rag.last_indexed is not None
    assert "一只猫坐在窗台上" in rag.last_indexed["user_message"]


def test_graph_image_reply_without_vision_keeps_placeholder(tmp_path):
    rag = StubRagService()
    llm = ScriptedLLM([AIMessage(content="我看不到图")])
    graph, _ = asyncio.run(
        create_graph(llm, BotConfig(rag_enabled=True), db_dir=str(tmp_path), rag_service=rag)
    )
    state = {
        **_initial_state(),
        "content_kind": "image",
        "raw_content": '<img src="https://x/1.jpg"/>',
        "llm_text": "[图片]",
        "image_srcs": ["https://x/1.jpg"],
    }
    result = asyncio.run(graph.ainvoke(state, {"configurable": {"thread_id": "test:thread"}}))

    assert result["reply_text"] == "我看不到图"
    humans = [m for m in result["messages"] if isinstance(m, HumanMessage)]
    assert humans and humans[0].content == "[图片]"  # 占位符保留
    assert rag.last_indexed is None  # 纯图片无描述 → 不入库
