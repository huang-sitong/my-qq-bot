"""skill_manager 节点：从 AIMessage tool_calls 更新 active_skills。"""

import asyncio

from langchain_core.messages import AIMessage, ToolMessage

from bot.package.orchestration.nodes.action_node.skill_manager import skill_manager_node
from bot.package.skill import Skill, SkillRegistry
from tests.fakes import make_state


def _registry():
    return SkillRegistry({
        "translate": Skill(name="translate", description="中英互译", body="body"),
    })


def _load_call(skill_name, call_id="c1"):
    return AIMessage(
        content="",
        tool_calls=[{"name": "load_skill", "args": {"skill_name": skill_name},
                     "id": call_id, "type": "tool_call"}],
    )


def test_loads_skill_into_active():
    state = make_state(messages=[
        _load_call("translate"),
        ToolMessage(content="正文", tool_call_id="c1"),
    ])
    result = asyncio.run(skill_manager_node(state, skill_registry=_registry()))
    assert result["active_skills"] == ["translate"]


def test_unloads_skill():
    state = make_state(active_skills=["translate"], messages=[
        AIMessage(content="", tool_calls=[
            {"name": "unload_skill", "args": {"skill_name": "translate"},
             "id": "c1", "type": "tool_call"}]),
        ToolMessage(content="已停用", tool_call_id="c1"),
    ])
    result = asyncio.run(skill_manager_node(state, skill_registry=_registry()))
    assert result["active_skills"] == []


def test_ignores_nonexistent_skill():
    state = make_state(active_skills=["translate"], messages=[
        _load_call("ghost"),
        ToolMessage(content="不存在", tool_call_id="c1"),
    ])
    result = asyncio.run(skill_manager_node(state, skill_registry=_registry()))
    assert result == {}  # 不写入 ghost，active 保持不变
    assert state["active_skills"] == ["translate"]


def test_skips_duplicate_load():
    state = make_state(active_skills=["translate"], messages=[_load_call("translate")])
    result = asyncio.run(skill_manager_node(state, skill_registry=_registry()))
    assert result == {}


def test_noop_without_skill_calls():
    state = make_state(messages=[
        AIMessage(content="", tool_calls=[
            {"name": "search_chat_history", "args": {"query": "x"},
             "id": "c1", "type": "tool_call"}]),
    ])
    result = asyncio.run(skill_manager_node(state, skill_registry=_registry()))
    assert result == {}


def test_noop_with_no_tool_calls():
    state = make_state(messages=[AIMessage(content="普通回复")])
    result = asyncio.run(skill_manager_node(state, skill_registry=_registry()))
    assert result == {}
