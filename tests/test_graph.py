import asyncio

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from bot.core.graph import create_graph
from common import BotConfig
from tests.fakes import FakeVisionService, ScriptedLLM, StubMemoryStore, StubRagService

TOOL_CALLS = [
    {"name": "search_chat_history", "args": {"query": "之前聊了什么"}, "id": "call_1", "type": "tool_call"},
]

SAMPLE = [
    {"thread_id": "test:thread", "sender_id": "u1", "sender_name": "张三",
     "receiver_id": "bot1", "receiver_name": "小助手",
     "content": "上次我们决定用 qwen3-embedding", "timestamp": "2026-07-30 09:00:00",
     "score": 0.85},
]


def _initial_state() -> dict:
    # channel_type=1 (DIRECT) → Router 判 reply；graph 直接消费输入 HumanMessage
    return {
        "messages": [HumanMessage(
            content="还记得我们聊过 RAG 吗？",
            name="张三",
            additional_kwargs={"user_id": "u1", "user_name": "张三"},
        )],
        "thread_id": "test:thread",
        "channel_id": "private:u1",
        "persona": "你是{bot_name}",
        "reply_text": "",
        "should_respond": False,
        "bot_name": "测试机器人",
        "bot_id": "bot1",
        "channel_type": 1,
        "tool_rounds": 0,
        "content_kind": "text",
        "llm_text": "还记得我们聊过 RAG 吗？",
        "clean_text": "还记得我们聊过 RAG 吗？",
        "vision_target_count": 1,
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
        create_graph(llm, BotConfig(_env_file=None, rag_enabled=True), db_dir=str(tmp_path), rag_service=rag)
    )

    result = asyncio.run(graph.ainvoke(_initial_state(), {"configurable": {"thread_id": "test:thread"}}))

    assert result["reply_text"] == "我们上次决定用 qwen3-embedding 做嵌入"
    # 循环确实发生：state 中应包含 ToolMessage，且 stub 检索结果流入其 content
    tool_msgs = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert tool_msgs, "expected a ToolMessage from the tool loop"
    assert "上次我们决定用 qwen3-embedding" in tool_msgs[0].content


def test_graph_injects_current_time_hint(tmp_path):
    llm = ScriptedLLM([AIMessage(content="好的")])
    graph, _ = asyncio.run(
        create_graph(llm, BotConfig(_env_file=None), db_dir=str(tmp_path))
    )
    asyncio.run(graph.ainvoke(_initial_state(), {"configurable": {"thread_id": "test:thread"}}))

    # 时间提示作为 SystemMessage 注入（LLM 计算相对时间/hours/时间窗的基准）
    sys_msgs = [m for m in llm.last_messages if isinstance(m, SystemMessage)]
    assert any("当前时间" in m.content for m in sys_msgs)


def test_graph_memory_tool_roundtrip(tmp_path):
    store = StubMemoryStore()
    asyncio.run(store.store_memory("u1", "名字", "张三"))
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

def test_graph_does_not_index_turn(tmp_path):
    rag = StubRagService()
    llm = ScriptedLLM([AIMessage(content="收到")])
    graph, _ = asyncio.run(
        create_graph(llm, BotConfig(rag_enabled=True), db_dir=str(tmp_path), rag_service=rag)
    )
    result = asyncio.run(graph.ainvoke(_initial_state(), {"configurable": {"thread_id": "test:thread"}}))
    assert result["reply_text"] == "收到"
    assert rag.last_indexed is None


def test_graph_input_message_appends_to_checkpoint(tmp_path):
    async def run():
        llm = ScriptedLLM([AIMessage(content="a"), AIMessage(content="b")])
        graph, checkpointer = await create_graph(
            llm, BotConfig(_env_file=None), db_dir=str(tmp_path)
        )
        try:
            cfg = {"configurable": {"thread_id": "test:thread"}}
            await graph.ainvoke(_initial_state(), cfg)
            second = _initial_state()
            second["messages"] = [HumanMessage(content="第二条")]
            await graph.ainvoke(second, cfg)
            snapshot = await graph.aget_state(cfg)
            assert len(snapshot.values["messages"]) == 4
        finally:
            await checkpointer.conn.close()

    asyncio.run(run())


def test_graph_does_not_reprocess_previous_image_on_later_turn(tmp_path):
    async def run():
        vision = FakeVisionService(["一只猫坐在窗台上"])
        llm = ScriptedLLM([AIMessage(content="好可爱的猫！"), AIMessage(content="好的")])
        graph, checkpointer = await create_graph(
            llm, BotConfig(_env_file=None), db_dir=str(tmp_path),
            vision_service=vision,
        )
        try:
            cfg = {"configurable": {"thread_id": "test:thread"}}
            first = {
                **_initial_state(),
                "content_kind": "image",
                "clean_text": "",
                "llm_text": "[图片]",
                "messages": [HumanMessage(
                    content="[图片]",
                    name="张三",
                    additional_kwargs={
                        "user_id": "u1",
                        "image_srcs": ["https://x/1.jpg"],
                    },
                )],
            }
            await graph.ainvoke(first, cfg)

            second = {
                **_initial_state(),
                "messages": [HumanMessage(
                    content="第二条",
                    name="张三",
                    additional_kwargs={
                        "user_id": "u1",
                        "image_srcs": [],
                    },
                )],
            }
            await graph.ainvoke(second, cfg)

            assert vision.calls == 1
        finally:
            await checkpointer.conn.close()

    asyncio.run(run())


def test_graph_image_reply_includes_vision_description(tmp_path):
    async def run():
        rag = StubRagService()
        vision = FakeVisionService(["一只猫坐在窗台上"])
        llm = ScriptedLLM([AIMessage(content="好可爱的猫！")])
        graph, checkpointer = await create_graph(
            llm, BotConfig(rag_enabled=True), db_dir=str(tmp_path),
            rag_service=rag, vision_service=vision,
        )
        try:
            state = {
                **_initial_state(),
                "content_kind": "image",
                "clean_text": "",
                "llm_text": "[图片]",
                "messages": [HumanMessage(
                    content="[图片]",
                    name="张三",
                    additional_kwargs={
                        "user_id": "u1",
                        "image_srcs": ["https://x/1.jpg"],
                    },
                )],
            }
            result = await graph.ainvoke(
                state, {"configurable": {"thread_id": "test:thread"}}
            )

            assert result["reply_text"] == "好可爱的猫！"
            humans = [m for m in result["messages"] if isinstance(m, HumanMessage)]
            assert humans and humans[0].content == "[图片：一只猫坐在窗台上]"
            assert rag.last_indexed is None
        finally:
            await checkpointer.conn.close()

    asyncio.run(run())


def test_graph_image_reply_without_vision_keeps_placeholder(tmp_path):
    async def run():
        rag = StubRagService()
        llm = ScriptedLLM([AIMessage(content="我看不到图")])
        graph, checkpointer = await create_graph(
            llm, BotConfig(_env_file=None, rag_enabled=True), db_dir=str(tmp_path),
            rag_service=rag,
        )
        try:
            state = {
                **_initial_state(),
                "content_kind": "image",
                "clean_text": "",
                "llm_text": "[图片]",
                "messages": [HumanMessage(
                    content="[图片]",
                    name="张三",
                    additional_kwargs={
                        "user_id": "u1",
                        "image_srcs": ["https://x/1.jpg"],
                    },
                )],
            }
            result = await graph.ainvoke(
                state, {"configurable": {"thread_id": "test:thread"}}
            )

            assert result["reply_text"] == "我看不到图"
            humans = [m for m in result["messages"] if isinstance(m, HumanMessage)]
            assert humans and humans[0].content == "[图片]"  # 占位符保留
            assert rag.last_indexed is None
        finally:
            await checkpointer.conn.close()

    asyncio.run(run())


# ----------------------------------------------------------------------
# 技能模块集成：加载持久化 + 线程隔离 + 注入可见。
# ----------------------------------------------------------------------
# 注意：AsyncSqliteSaver 的 asyncio.Lock 绑定首个事件循环（同 test_memory_store
# 约定），跨轮/跨线程 ainvoke 必须放在单个 asyncio.run 内——与真实 bot 单 loop 一致。

from skill import Skill, SkillRegistry

SKILL_LOAD_CALLS = [
    {"name": "load_skill", "args": {"skill_name": "translate"},
     "id": "call_skill_1", "type": "tool_call"},
]


def _skill_registry():
    return SkillRegistry({
        "translate": Skill(name="translate", description="中英互译", body="翻译规则：保留语气"),
    })


def test_graph_skill_persists_across_turns(tmp_path):
    async def run():
        llm = ScriptedLLM([
            # 第 1 轮：加载技能
            AIMessage(content="", tool_calls=SKILL_LOAD_CALLS),
            AIMessage(content="已启用翻译技能"),
            # 第 2 轮（同 thread）：直接翻译
            AIMessage(content="翻译：你好 → Hello"),
        ])
        graph, checkpointer = await create_graph(
            llm, BotConfig(skills_enabled=True), db_dir=str(tmp_path),
            skill_registry=_skill_registry(),
        )
        try:
            state1 = {**_initial_state(), "llm_text": "用翻译技能", "clean_text": "用翻译技能"}
            r1 = await graph.ainvoke(state1, {"configurable": {"thread_id": "test:thread"}})
            assert r1["active_skills"] == ["translate"]

            # 第 2 轮不带 active_skills（输入覆盖 checkpoint 会导致清零——这是设计约束）
            state2 = {**_initial_state(), "llm_text": "翻译 how are you", "clean_text": "翻译 how are you"}
            r2 = await graph.ainvoke(state2, {"configurable": {"thread_id": "test:thread"}})
            assert r2["active_skills"] == ["translate"]  # checkpoint 恢复
            sys_msgs = [m for m in llm.last_messages if isinstance(m, SystemMessage)]
            assert any("翻译规则：保留语气" in m.content for m in sys_msgs)  # 正文注入可见
        finally:
            await checkpointer.conn.close()

    asyncio.run(run())


def test_graph_skill_isolated_per_thread(tmp_path):
    async def run():
        llm = ScriptedLLM([
            AIMessage(content="", tool_calls=SKILL_LOAD_CALLS),
            AIMessage(content="已启用"),
            AIMessage(content="普通回复"),  # 线程 B
        ])
        graph, checkpointer = await create_graph(
            llm, BotConfig(skills_enabled=True), db_dir=str(tmp_path),
            skill_registry=_skill_registry(),
        )
        try:
            a = {**_initial_state(), "llm_text": "翻译", "clean_text": "翻译"}
            await graph.ainvoke(a, {"configurable": {"thread_id": "thread:A"}})

            b = {**_initial_state(), "llm_text": "你好", "clean_text": "你好"}
            rb = await graph.ainvoke(b, {"configurable": {"thread_id": "thread:B"}})
            assert rb.get("active_skills", []) == []  # 新线程不串技能
        finally:
            await checkpointer.conn.close()

    asyncio.run(run())


# ----------------------------------------------------------------------
# run_bash 工具：图内 ToolNode 端到端 + hint 注入。
# ----------------------------------------------------------------------

class _FakeProc:
    returncode = 0

    async def communicate(self):
        return b"hello from bash\n", b""

    def kill(self):
        pass


def test_graph_runs_bash_tool(tmp_path, monkeypatch):
    """run_bash 工具经图内 ToolNode 执行并回环：tool_call → ToolMessage。"""
    async def fake_exec(*args, **kwargs):
        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    llm = ScriptedLLM([
        AIMessage(content="", tool_calls=[
            {"name": "run_bash", "args": {"command": "echo hi"},
             "id": "call_bash", "type": "tool_call"},
        ]),
        AIMessage(content="已执行"),
    ])
    graph, _ = asyncio.run(
        create_graph(llm, BotConfig(_env_file=None), db_dir=str(tmp_path))
    )
    result = asyncio.run(graph.ainvoke(_initial_state(), {"configurable": {"thread_id": "test:thread"}}))

    assert result["reply_text"] == "已执行"
    tool_msgs = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert tool_msgs
    assert "hello from bash" in tool_msgs[0].content


def test_graph_injects_bash_hint(tmp_path):
    llm = ScriptedLLM([AIMessage(content="好的")])
    graph, _ = asyncio.run(
        create_graph(llm, BotConfig(_env_file=None), db_dir=str(tmp_path))
    )
    asyncio.run(graph.ainvoke(_initial_state(), {"configurable": {"thread_id": "test:thread"}}))
    sys_msgs = [m for m in llm.last_messages if isinstance(m, SystemMessage)]
    assert any("run_bash" in m.content for m in sys_msgs)


# ----------------------------------------------------------------------
# send_file 工具：图内 ToolNode 端到端 + hint 注入。
# ----------------------------------------------------------------------

class _FakeFileSender:
    def __init__(self):
        self.calls = []

    async def send_file(self, channel_id, path, name):
        self.calls.append((channel_id, path, name))
        return {"status": "ok", "data": {"file_id": "f1"}}


def test_graph_runs_send_file_tool(tmp_path):
    sender = _FakeFileSender()
    path = tmp_path / "chapter.zip"
    path.write_bytes(b"zip")

    llm = ScriptedLLM([
        AIMessage(content="", tool_calls=[
            {"name": "send_file", "args": {"path": str(path)},
             "id": "call_file", "type": "tool_call"},
        ]),
        AIMessage(content="已发送"),
    ])
    graph, _ = asyncio.run(
        create_graph(
            llm, BotConfig(_env_file=None, bash_allowed_roots=[str(tmp_path)]),
            db_dir=str(tmp_path),
            file_sender=sender,
        )
    )
    result = asyncio.run(graph.ainvoke(
        _initial_state(), {"configurable": {"thread_id": "test:thread"}},
    ))

    assert result["reply_text"] == "已发送"
    tool_msgs = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert tool_msgs
    assert "文件已发送" in tool_msgs[0].content
    assert sender.calls[0][0] == "private:u1"


def test_graph_injects_file_send_hint(tmp_path):
    llm = ScriptedLLM([AIMessage(content="好的")])
    graph, _ = asyncio.run(
        create_graph(
            llm, BotConfig(_env_file=None), db_dir=str(tmp_path),
            file_sender=_FakeFileSender(),
        )
    )
    asyncio.run(graph.ainvoke(
        _initial_state(), {"configurable": {"thread_id": "test:thread"}},
    ))
    sys_msgs = [m for m in llm.last_messages if isinstance(m, SystemMessage)]
    assert any("send_file" in m.content for m in sys_msgs)
