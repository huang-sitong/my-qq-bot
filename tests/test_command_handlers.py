"""V1 内置命令 handler 输出测试。"""

import asyncio
import time

from commands import (
    CommandActor,
    CommandContext,
    CommandServices,
    build_command_registry,
)
from common import BotConfig
from skill import Skill, SkillRegistry


def _services(skills=None):
    return CommandServices(
        version="1.2.3",
        started_at=time.time() - 65,
        bot_name="test-bot",
        skill_registry=skills,
        mcp_tool_count=2,
    )


def _ctx(services, args=(), actor=None):
    return CommandContext(
        raw="/" + " ".join(args),
        actor=actor or CommandActor(user_id="u1", name="tester", is_admin=False),
        platform="test",
        guild_id="",
        channel_id="ch1",
        thread_id="t1",
        channel_type=1,
        args=args,
        config=BotConfig(_env_file=None, admin_ids=["admin1"], llm_model="deepseek-v4-flash"),
        services=services,
    )


def _execute(registry, services, name, args=(), actor=None):
    command = registry.resolve(name)
    ctx = _ctx(services, args=args, actor=actor)
    return asyncio.run(command.handler(ctx))


def test_help_lists_all_commands():
    services = _services()
    registry = build_command_registry(services)
    result = _execute(registry, services, "help")
    assert "/help" in result.text
    assert "/ping" in result.text


def test_help_shows_single_command():
    services = _services()
    registry = build_command_registry(services)
    result = _execute(registry, services, "help", ("status",))
    assert "/status" in result.text
    assert "管理员" in result.text


def test_ping():
    services = _services()
    registry = build_command_registry(services)
    result = _execute(registry, services, "ping")
    assert result.text == "Pong."


def test_version():
    services = _services()
    registry = build_command_registry(services)
    result = _execute(registry, services, "version")
    assert result.text == "qq-bot 1.2.3"


def test_skills_empty():
    services = _services()
    registry = build_command_registry(services)
    result = _execute(registry, services, "skills")
    assert result.text == "当前没有可用技能。"


def test_skills_lists_index():
    skills = SkillRegistry({"x": Skill(name="x", description="描述", body="正文")})
    services = _services(skills)
    registry = build_command_registry(services)
    result = _execute(registry, services, "skills")
    assert "- x: 描述" in result.text


def test_skills_truncated_when_over_index_max():
    skills = SkillRegistry(
        {f"s{i}": Skill(name=f"s{i}", description="描述", body="正文") for i in range(10)},
        index_max=3,
    )
    services = _services(skills)
    registry = build_command_registry(services)
    result = _execute(registry, services, "skills")
    assert "- s0: 描述" in result.text
    assert "- s2: 描述" in result.text
    assert "- s3: 描述" not in result.text
    assert "共 10 个技能" in result.text


def test_skill_returns_description_and_body():
    skills = SkillRegistry({"x": Skill(name="x", description="描述", body="正文内容")})
    services = _services(skills)
    registry = build_command_registry(services)
    result = _execute(registry, services, "skill", ("x",))
    assert "描述" in result.text
    assert "正文内容" in result.text


def test_skill_body_is_truncated_to_2000_chars():
    skills = SkillRegistry({"x": Skill(name="x", description="描述", body="a" * 2001)})
    services = _services(skills)
    registry = build_command_registry(services)
    result = _execute(registry, services, "skill", ("x",))
    assert "a" * 2000 in result.text
    assert "a" * 2001 not in result.text


def test_skill_missing():
    services = _services(SkillRegistry({}))
    registry = build_command_registry(services)
    result = _execute(registry, services, "skill", ("missing",))
    assert result.text == "技能不存在。"


def test_skill_requires_arg():
    services = _services()
    registry = build_command_registry(services)
    result = _execute(registry, services, "skill")
    assert "用法" in result.text


def test_status_returns_safe_runtime_info():
    services = _services()
    registry = build_command_registry(services)
    result = _execute(registry, services, "status")
    assert "qq-bot 1.2.3" in result.text
    assert "deepseek-v4-flash" in result.text
    assert "db" in result.text
    assert "MCP：2 个工具" in result.text
    assert "API_KEY" not in result.text


def test_status_includes_metrics_when_provider_set():
    services = _services()
    services.metrics_provider = lambda: {
        "queue_size": 3,
        "processed": 10,
        "dropped_duplicates": 2,
    }
    registry = build_command_registry(services)
    result = _execute(registry, services, "status")
    assert "队列深度：3" in result.text
    assert "已处理消息：10" in result.text
    assert "丢弃重复：2" in result.text


def test_auto_reply_shows_state():
    services = _services()
    registry = build_command_registry(services)
    result = _execute(registry, services, "auto_reply")
    assert "当前状态：关闭" in result.text


def test_auto_reply_turns_on():
    services = _services()
    registry = build_command_registry(services)
    ctx = _ctx(services, args=("on",), actor=CommandActor(user_id="admin1", name="admin", is_admin=True))
    result = asyncio.run(registry.resolve("auto_reply").handler(ctx))
    assert result.text == "auto_reply 已开启。"
    assert ctx.config.auto_reply is True


def test_auto_reply_turns_off():
    services = _services()
    registry = build_command_registry(services)
    ctx = _ctx(services, args=("off",))
    result = asyncio.run(registry.resolve("auto_reply").handler(ctx))
    assert result.text == "auto_reply 已关闭。"
    assert ctx.config.auto_reply is False


def test_auto_reply_invalid_arg_returns_usage():
    services = _services()
    registry = build_command_registry(services)
    result = _execute(registry, services, "auto_reply", ("maybe",))
    assert "参数无效" in result.text


def test_auto_reply_is_admin_command():
    services = _services()
    registry = build_command_registry(services)
    assert registry.resolve("auto_reply").permission == "admin"
    assert "/auto_reply" in _execute(registry, services, "help").text  # /help 自动收录


def test_mcp_lists_loaded_tool_names():
    services = _services()
    services.mcp_tool_names = ("web_search", "read_file")
    registry = build_command_registry(services)
    result = _execute(registry, services, "mcp")
    assert "已加载 2 个 MCP 工具" in result.text
    assert "- web_search" in result.text
    assert "- read_file" in result.text


def test_mcp_empty():
    services = CommandServices(
        version="test", started_at=0.0, bot_name="",
        mcp_tool_count=0,
    )
    registry = build_command_registry(services)
    result = _execute(registry, services, "mcp")
    assert result.text == "当前未加载 MCP 工具。"


def test_state_commands_are_admin_commands():
    services = _services()
    registry = build_command_registry(services)
    assert registry.resolve("clear").permission == "admin"
    assert registry.resolve("compact").permission == "admin"
    assert registry.resolve("mcp").permission == "admin"
    assert registry.resolve("context").permission == "admin"
