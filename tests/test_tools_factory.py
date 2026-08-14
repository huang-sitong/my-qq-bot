"""build_tools 工具统一层测试：组装、schema 排除注入参数。"""

import asyncio
from pathlib import Path

from bot.core.skills import Skill, SkillRegistry
from bot.core.tools import build_tools
from bot.core.tools.run_bash import BashConfig
from tests.fakes import StubMemoryStore, StubRagService


def _names(tools):
    return {t.name for t in tools}


def test_rag_tool_present_when_rag_enabled():
    tools = build_tools(rag_service=StubRagService(), memory_store=StubMemoryStore())
    assert "search_chat_history" in _names(tools)


def test_no_rag_tool_when_disabled():
    tools = build_tools(rag_service=StubRagService(enabled=False), memory_store=None)
    assert "search_chat_history" not in _names(tools)


def test_memory_tools_present_when_store_injected():
    tools = build_tools(rag_service=None, memory_store=StubMemoryStore())
    assert {"remember_user_memory", "recall_user_memory"} <= _names(tools)


def test_mcp_tools_appended():
    class FakeMcpTool:
        name = "web_search"

    tools = build_tools(rag_service=None, memory_store=None, mcp_tools=[FakeMcpTool()])
    assert "web_search" in _names(tools)


def test_llm_schema_excludes_injected_args():
    tools = build_tools(rag_service=StubRagService(), memory_store=StubMemoryStore())
    by_name = {t.name: t for t in tools}

    search = by_name["search_chat_history"]
    props = search.tool_call_schema.model_json_schema()["properties"]
    assert "thread_id" not in props
    assert {"query", "user_name", "content_keyword", "start_time", "end_time", "hours"} <= set(props)

    recall = by_name["recall_user_memory"]
    recall_props = recall.tool_call_schema.model_json_schema()["properties"]
    assert "user_id" in recall_props
    assert "user_name" in recall_props
    assert "messages" not in recall_props
    assert "keyword" in recall_props


def test_llm_schema_has_param_descriptions():
    """每个参数必须有 description —— 回归手写 TOOL_SCHEMA 删除后的 schema 退化。"""
    tools = build_tools(rag_service=StubRagService(), memory_store=StubMemoryStore())
    by_name = {t.name: t for t in tools}

    search = by_name["search_chat_history"]
    search_props = search.tool_call_schema.model_json_schema()["properties"]
    assert "最近 N 小时" in search_props["hours"]["description"]
    assert "中文表述" in search_props["query"]["description"]

    remember = by_name["remember_user_memory"]
    remember_props = remember.tool_call_schema.model_json_schema()["properties"]
    assert "语义标签" in remember_props["key"]["description"]

    recall = by_name["recall_user_memory"]
    recall_props = recall.tool_call_schema.model_json_schema()["properties"]
    assert "模糊匹配" in recall_props["keyword"]["description"]
    assert "显示昵称" in recall_props["user_name"]["description"]
    assert "平台 ID" in recall_props["user_id"]["description"]


"""技能工具：registry 非空才注入；schema 无注入参数。"""


def test_skill_tools_present_when_registry_injected():
    registry = SkillRegistry({"translate": Skill(name="translate", description="中英互译", body="正文")})
    tools = build_tools(rag_service=None, memory_store=None, skill_registry=registry)
    assert {"load_skill", "unload_skill"} <= _names(tools)


def test_no_skill_tools_without_registry():
    tools = build_tools(rag_service=None, memory_store=None)
    assert "load_skill" not in _names(tools)


def test_no_skill_tools_when_empty_registry():
    tools = build_tools(rag_service=None, memory_store=None, skill_registry=SkillRegistry())
    assert "load_skill" not in _names(tools)


def test_load_skill_schema_only_has_skill_name():
    registry = SkillRegistry({"translate": Skill(name="translate", description="中英互译", body="正文")})
    tools = build_tools(rag_service=None, memory_store=None, skill_registry=registry)
    by_name = {t.name: t for t in tools}
    props = by_name["load_skill"].tool_call_schema.model_json_schema()["properties"]
    assert set(props) == {"skill_name"}


"""run_bash 工具：bash_config 启用才注入；schema 仅 command/cwd；shell 不存在降级。"""


def test_bash_tool_present_when_config_enabled():
    tools = build_tools(bash_config=BashConfig(enabled=True, project_root=Path(".")))
    assert "run_bash" in _names(tools)


def test_no_bash_tool_when_disabled_or_none():
    disabled = build_tools(bash_config=BashConfig(enabled=False, project_root=Path(".")))
    assert "run_bash" not in _names(disabled)
    assert "run_bash" not in _names(build_tools())


def test_bash_schema_has_command_cwd_and_timeout():
    tools = build_tools(bash_config=BashConfig(enabled=True, project_root=Path(".")))
    by_name = {t.name: t for t in tools}
    props = by_name["run_bash"].tool_call_schema.model_json_schema()["properties"]
    assert set(props) == {"command", "cwd", "timeout"}
    assert "命令" in props["command"]["description"]
    assert "工作目录" in props["cwd"]["description"]
    timeout_schema = props["timeout"]
    assert any(
        item.get("minimum") == 1 and item.get("maximum") == 3600
        for item in timeout_schema["anyOf"]
    )


def test_bash_tool_degrades_on_exception():
    """shell 不存在（FileNotFoundError）→ 工具返回「工具执行失败。」，不让异常崩 ToolNode。"""
    cfg = BashConfig(enabled=True, shell="no_such_shell", timeout=1, project_root=Path("."))
    tools = build_tools(bash_config=cfg)
    tool = {t.name: t for t in tools}["run_bash"]
    result = asyncio.run(tool.ainvoke({"command": "echo hi"}))
    assert result == "工具执行失败。"


"""send_file 工具：file_sender 与 send_roots 都注入才暴露；schema 无 channel_id。"""


class _FakeFileSender:
    async def send_file(self, channel_id, path, name):
        return {"status": "ok"}


def test_send_file_tool_present_when_sender_and_roots_injected():
    tools = build_tools(
        file_sender=_FakeFileSender(),
        send_roots=[Path.cwd()],
    )
    assert "send_file" in _names(tools)


def test_no_send_file_tool_without_sender_or_roots():
    assert "send_file" not in _names(build_tools())
    assert "send_file" not in _names(build_tools(
        file_sender=_FakeFileSender(), send_roots=None,
    ))


def test_send_file_schema_only_has_path_and_name():
    tools = build_tools(
        file_sender=_FakeFileSender(),
        send_roots=[Path.cwd()],
    )
    props = {t.name: t for t in tools}["send_file"].tool_call_schema.model_json_schema()["properties"]
    assert set(props) == {"path", "name"}
    assert "路径" in props["path"]["description"]
