import asyncio
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage

from bot.package.config import BotConfig
from bot.package.orchestration.nodes import call_llm_node
from bot.package.tools import build_tools
from bot.package.tools.builtin.run_bash import BashConfig
from tests.fakes import ScriptedLLM, StubMemoryStore, StubRagService, make_state

TOOL_CALLS = [
    {"name": "search_chat_history", "args": {"query": "x"}, "id": "call_1", "type": "tool_call"},
]
MEMORY_CALLS = [
    {"name": "recall_user_memory", "args": {"keyword": "食物"}, "id": "call_2", "type": "tool_call"},
]
BASE = make_state(messages=[HumanMessage(content="你好")])
CONFIG_ON = BotConfig(rag_enabled=True)


def _full_tools():
    return build_tools(rag_service=StubRagService(), memory_store=StubMemoryStore())


def _memory_tools():
    return build_tools(rag_service=None, memory_store=StubMemoryStore())


def test_returns_tool_calls_when_tools_bound():
    llm = ScriptedLLM([AIMessage(content="", tool_calls=TOOL_CALLS)])
    state = BASE | {"tool_rounds": 0}
    result = asyncio.run(call_llm_node(
        state, llm=llm, tools=_full_tools(), use_memory=True, bot_config=CONFIG_ON,
    ))
    assert result["reply_text"] == ""
    assert result["tool_rounds"] == 1
    assert result["messages"][0].tool_calls


def test_returns_tool_calls_when_only_memory_tools():
    llm = ScriptedLLM([AIMessage(content="", tool_calls=MEMORY_CALLS)])
    state = BASE | {"tool_rounds": 0}
    result = asyncio.run(call_llm_node(
        state, llm=llm, tools=_memory_tools(), use_memory=True, bot_config=CONFIG_ON,
    ))
    assert result["messages"][0].tool_calls
    assert result["tool_rounds"] == 1


def test_returns_final_reply_when_no_tool_calls():
    llm = ScriptedLLM([AIMessage(content="最终回复")])
    state = BASE | {"tool_rounds": 0}
    result = asyncio.run(call_llm_node(
        state, llm=llm, tools=_full_tools(), use_memory=True, bot_config=CONFIG_ON,
    ))
    assert result["reply_text"] == "最终回复"
    assert "tool_rounds" not in result
    assert not result["messages"][0].tool_calls


def test_plain_path_when_no_tools():
    llm = ScriptedLLM([AIMessage(content="普通")])
    result = asyncio.run(call_llm_node(BASE, llm=llm, tools=None, bot_config=CONFIG_ON))
    assert result["reply_text"] == "普通"


def test_multimodal_list_reply_normalized_to_text():
    """多模态主 LLM 返回 content 块列表时，reply_text 归一化为纯文本字符串（防下游 .strip() 崩溃）。"""
    llm = ScriptedLLM([AIMessage(content=[{"type": "text", "text": "这是一只猫"}])])
    state = BASE | {"tool_rounds": 0}
    result = asyncio.run(call_llm_node(
        state, llm=llm, tools=_full_tools(), use_memory=True, bot_config=CONFIG_ON,
    ))
    assert result["reply_text"] == "这是一只猫"
    assert isinstance(result["reply_text"], str)


def test_plain_path_normalizes_multimodal_list_content():
    """无工具路径同样归一化：多模态 content 块列表 → 纯文本 reply_text。"""
    llm = ScriptedLLM([AIMessage(content=[{"type": "text", "text": "纯文本回复"}])])
    result = asyncio.run(call_llm_node(BASE, llm=llm, tools=None, bot_config=CONFIG_ON))
    assert result["reply_text"] == "纯文本回复"
    assert isinstance(result["reply_text"], str)


def test_plain_path_when_rounds_exhausted():
    llm = ScriptedLLM([AIMessage(content="耗尽后收尾")])
    state = BASE | {"tool_rounds": 1}
    config = BotConfig(rag_enabled=True, rag_max_agent_rounds=1)
    result = asyncio.run(call_llm_node(
        state, llm=llm, tools=_full_tools(), use_memory=True, bot_config=config,
    ))
    assert result["reply_text"] == "耗尽后收尾"
    assert not result["messages"][0].tool_calls


def test_memory_hint_injected_when_use_memory():
    llm = ScriptedLLM([AIMessage(content="好")])
    state = BASE | {"tool_rounds": 0}
    asyncio.run(call_llm_node(
        state, llm=llm, tools=_memory_tools(), use_memory=True, bot_config=CONFIG_ON,
    ))
    assert any(
        "recall_user_memory" in getattr(m, "content", "")
        for m in llm.last_messages
    )


def test_mcp_hint_injected_when_use_mcp():
    llm = ScriptedLLM([AIMessage(content="好")])
    state = BASE | {"tool_rounds": 0}
    asyncio.run(call_llm_node(
        state, llm=llm, tools=_full_tools(), use_memory=False, use_mcp=True,
        bot_config=CONFIG_ON,
    ))
    assert any(
        "外部工具" in getattr(m, "content", "")
        for m in llm.last_messages
    )


def test_mcp_hint_not_injected_when_disabled():
    llm = ScriptedLLM([AIMessage(content="好")])
    state = BASE | {"tool_rounds": 0}
    asyncio.run(call_llm_node(
        state, llm=llm, tools=_full_tools(), use_memory=True, use_mcp=False,
        bot_config=CONFIG_ON,
    ))
    assert not any(
        "外部工具" in getattr(m, "content", "")
        for m in llm.last_messages
    )


def _bash_tools():
    return build_tools(bash_config=BashConfig(enabled=True, project_root=Path(".")))


def test_bash_hint_injected_when_use_bash():
    llm = ScriptedLLM([AIMessage(content="好")])
    state = BASE | {"tool_rounds": 0}
    asyncio.run(call_llm_node(
        state, llm=llm, tools=_bash_tools(), use_memory=False, use_bash=True,
        bot_config=CONFIG_ON,
    ))
    assert any("run_bash" in getattr(m, "content", "") for m in llm.last_messages)


def test_bash_hint_not_injected_when_disabled():
    llm = ScriptedLLM([AIMessage(content="好")])
    state = BASE | {"tool_rounds": 0}
    asyncio.run(call_llm_node(
        state, llm=llm, tools=_bash_tools(), use_memory=False, use_bash=False,
        bot_config=CONFIG_ON,
    ))
    assert not any("run_bash" in getattr(m, "content", "") for m in llm.last_messages)


class _FakeFileSender:
    async def send_file(self, channel_id, path, name):
        return {"status": "ok"}


def _file_send_tools():
    return build_tools(
        file_sender=_FakeFileSender(),
        send_roots=[Path.cwd()],
    )


def test_file_send_hint_injected_when_use_file_send():
    llm = ScriptedLLM([AIMessage(content="好")])
    state = BASE | {"tool_rounds": 0}
    asyncio.run(call_llm_node(
        state, llm=llm, tools=_file_send_tools(), use_memory=False,
        use_file_send=True, bot_config=CONFIG_ON,
    ))
    assert any("send_file" in getattr(m, "content", "") for m in llm.last_messages)


def test_file_send_hint_not_injected_when_disabled():
    llm = ScriptedLLM([AIMessage(content="好")])
    state = BASE | {"tool_rounds": 0}
    asyncio.run(call_llm_node(
        state, llm=llm, tools=_file_send_tools(), use_memory=False,
        use_file_send=False, bot_config=CONFIG_ON,
    ))
    assert not any("send_file" in getattr(m, "content", "") for m in llm.last_messages)


# --- parallel_tool_calls ---

class _BindAwareLLM(ScriptedLLM):
    """记录 bind_tools kwargs 并在 ainvoke 时可见；可模拟服务商拒绝参数。"""

    def __init__(self, responses):
        super().__init__(responses)
        self._pending_bind_kwargs: dict = {}
        self.ainvoke_kwargs_seen: list[dict] = []

    def bind_tools(self, tools, **kwargs):
        super().bind_tools(tools, **kwargs)
        self._pending_bind_kwargs = dict(kwargs)
        return self

    async def ainvoke(self, messages, **kwargs):
        merged = {**self._pending_bind_kwargs, **kwargs}
        self.ainvoke_kwargs_seen.append(merged)
        if merged.get("parallel_tool_calls"):
            raise RuntimeError(
                "400 parallel_tool_calls is not supported by this provider",
            )
        return await super().ainvoke(messages, **kwargs)


def _parallel_config(parallel: bool) -> BotConfig:
    """显式 init 参数 + _env_file=None：隔离真实 .env / 环境变量，保证确定性。"""
    return BotConfig(
        rag_enabled=True,
        llm_parallel_tool_calls=parallel,
        _env_file=None,
    )


def test_parallel_tool_calls_passed_when_enabled():
    llm = ScriptedLLM([AIMessage(content="好")])
    state = BASE | {"tool_rounds": 0}
    asyncio.run(call_llm_node(
        state, llm=llm, tools=_full_tools(), bot_config=_parallel_config(True),
    ))
    assert llm.last_bind_kwargs == {"parallel_tool_calls": True}


def test_parallel_tool_calls_not_passed_when_disabled():
    llm = ScriptedLLM([AIMessage(content="好")])
    state = BASE | {"tool_rounds": 0}
    asyncio.run(call_llm_node(
        state, llm=llm, tools=_full_tools(), bot_config=_parallel_config(False),
    ))
    assert "parallel_tool_calls" not in (llm.last_bind_kwargs or {})


def test_parallel_tool_calls_degrades_when_provider_rejects():
    """报错文本含参数名 → 自动降级重试一次普通绑定并成功。"""
    llm = _BindAwareLLM([AIMessage(content="降级成功")])
    state = BASE | {"tool_rounds": 0}
    result = asyncio.run(call_llm_node(
        state, llm=llm, tools=_full_tools(), bot_config=_parallel_config(True),
    ))
    assert result["reply_text"] == "降级成功"
    assert llm.ainvoke_kwargs_seen[0].get("parallel_tool_calls") is True
    assert "parallel_tool_calls" not in llm.ainvoke_kwargs_seen[1]


def test_unrelated_llm_error_still_returns_apology_without_retry():
    """与参数无关的异常不触发降级重试，走原有道歉回退。"""

    class _BoomLLM(ScriptedLLM):
        async def ainvoke(self, messages, **kwargs):
            raise TimeoutError("gateway timeout")

    llm = _BoomLLM([])
    state = BASE | {"tool_rounds": 0}
    result = asyncio.run(call_llm_node(
        state, llm=llm, tools=_full_tools(), bot_config=_parallel_config(True),
    ))
    assert result["reply_text"] == "我暂时无法思考，请稍后再试"
