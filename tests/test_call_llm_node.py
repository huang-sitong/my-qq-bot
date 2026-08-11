import asyncio
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage

from bot.core.nodes import call_llm_node
from bot.core.tools import build_tools
from bot.core.tools.run_bash import BashConfig
from common import BotConfig
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
