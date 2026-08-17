"""领域数据对象统一从各限界上下文导出。"""

from pathlib import Path

import commands as core_commands
import conversation.bash as core_run_bash
import conversation.router as core_router
import knowledge.domain as core_knowledge_domain
import skill as core_skills
import vision as core_vision
from commands import (
    Command,
    CommandActor,
    CommandContext,
    CommandResult,
    CommandServices,
    ParsedCommand,
)
from conversation import (
    BashConfig,
    BotIdentity,
    RouteAction,
    RouteDecision,
)
from domain import ImageDescription, IndexTurnTask
from skill import Skill


def test_data_objects_are_single_source_in_contexts():
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


def test_shared_dtos_are_owned_by_domain_and_re_exported():
    assert core_knowledge_domain.IndexTurnTask is IndexTurnTask
    assert core_vision.ImageDescription is ImageDescription
    assert IndexTurnTask(thread_id="t", user_id="u", user_name="", bot_id="", bot_name="", user_message="", bot_reply="").thread_id == "t"
    assert ImageDescription(image_src="x", description="y").description == "y"
