"""领域数据对象统一从 ``domain`` 导出，旧 bot.core 路径保留兼容。"""

from pathlib import Path

import bot.core.commands as core_commands
import bot.core.router as core_router
import bot.core.skills as core_skills
import bot.core.tools.run_bash as core_run_bash
from domain import (
    BashConfig,
    BotIdentity,
    Command,
    CommandActor,
    CommandContext,
    CommandResult,
    CommandServices,
    ParsedCommand,
    RouteAction,
    RouteDecision,
    Skill,
)


def test_data_objects_are_single_source_in_object():
    assert core_commands.Command is Command
    assert core_commands.CommandActor is CommandActor
    assert core_commands.CommandContext is CommandContext
    assert core_commands.CommandResult is CommandResult
    assert core_commands.CommandServices is CommandServices
    assert core_router.RouteAction is RouteAction
    assert core_router.RouteDecision is RouteDecision
    assert core_skills.Skill is Skill
    assert core_run_bash.BashConfig is BashConfig


def test_bot_identity_is_shared_domain_object():
    identity = BotIdentity(id="bot1", name="小助手")
    assert identity.id == "bot1"
    assert identity.name == "小助手"


def test_data_objects_remain_usable():
    skill = Skill(name="translate", description="翻译", body="规则")
    parsed = ParsedCommand(name="ping")
    route = RouteDecision(action=RouteAction.IGNORE)
    bash = BashConfig(enabled=True, project_root=Path("."))
    assert skill.body == "规则"
    assert parsed.name == "ping"
    assert route.action == RouteAction.IGNORE
    assert bash.enabled is True
