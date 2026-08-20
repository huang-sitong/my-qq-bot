"""领域数据对象统一从各限界上下文导出。"""

from pathlib import Path

import bot.package.commands as core_commands
import bot.package.conversation.router as core_router
import bot.package.domain.bash as core_run_bash

# removed shim import
import bot.package.skill as core_skills
from bot.package.commands import (
    Command,
    CommandActor,
    CommandContext,
    CommandResult,
    CommandServices,
    ParsedCommand,
)
from bot.package.conversation import (
    BotIdentity,
    RouteAction,
    RouteDecision,
)
from bot.package.domain import BashConfig, ImageDescription, IndexTurnTask
from bot.package.skill import Skill


def test_data_objects_are_single_source_in_contexts():
    assert core_commands.Command is Command
    assert core_commands.CommandActor is CommandActor
    assert core_commands.CommandContext is CommandContext
    assert core_commands.CommandResult is CommandResult
    assert core_commands.CommandServices is CommandServices
    assert core_router.RouteAction is RouteAction
    assert core_router.RouteDecision is RouteDecision
    assert core_skills.Skill is Skill
    # BashConfig 已迁移至 tools.domain，domain 侧为兼容垫片（duplicate），仅校验同名同构
    assert core_run_bash.BashConfig.__name__ == BashConfig.__name__ == "BashConfig"
    assert core_run_bash.BashConfig(enabled=True).shell == BashConfig(enabled=True).shell


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
    # 垫片已移除，domain 为唯一源
    assert IndexTurnTask(thread_id="t", user_id="u", user_name="", bot_id="", bot_name="", user_message="", bot_reply="").thread_id == "t"
    assert ImageDescription(image_src="x", description="y").description == "y"
    # 知识/视觉上下文不再 re-export，需从 domain 单一导入
    import pathlib
    assert not pathlib.Path("src/bot/package/knowledge/domain.py").exists()
    assert not pathlib.Path("src/bot/package/vision/domain.py").exists()

def test_bash_config_lives_in_tools_domain():
    from bot.package.tools.domain import BashConfig as NewBash
    assert NewBash(enabled=True).shell == "bash"
    # 舊路徑應已遷移（墊片期：domain 仍可導入但發 DeprecationWarning；以文件內容為準，避免模塊緩存導致二次導入不 warning）
    import pathlib
    bash_shim = pathlib.Path("src/bot/package/domain/bash.py").read_text(encoding="utf-8")
    assert "DeprecationWarning" in bash_shim
    assert "bot.package.tools.domain" in bash_shim
    # 舊路徑仍可用（墊片期），新舊類同名同結構
    from bot.package.domain import BashConfig as Old
    assert Old.__name__ == "BashConfig"
    assert Old(enabled=True).shell == "bash"


def test_prompts_split():
    from bot.package.knowledge.prompts import RETRIEVAL_TASK
    from bot.package.orchestration.prompts import BASH_TOOL_HINT, SUMMARY_PROMPT
    assert "{old_summary}" in SUMMARY_PROMPT
    assert "run_bash" in BASH_TOOL_HINT
    assert RETRIEVAL_TASK.startswith("检索")


def test_constants_split():
    from bot.package.orchestration.constants import EXTERNAL_UPDATE_NODE
    from bot.package.platform.satori.constants import DIRECT_CHANNEL_TYPE
    assert EXTERNAL_UPDATE_NODE == "describe_image"
    assert DIRECT_CHANNEL_TYPE == 1


def test_config_no_longer_imports_domain():
    import ast
    import pathlib
    tree = ast.parse(pathlib.Path("src/bot/package/config/settings.py").read_text(encoding="utf-8"))
    imports = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module]
    assert not any(m.startswith("bot.package.domain") for m in imports if m)

def test_domain_init_has_no_getattr_magic():
    import ast
    import pathlib
    src = pathlib.Path("src/bot/package/domain/__init__.py").read_text(encoding="utf-8")
    assert "__getattr__" not in src
    assert "_module_map" not in src
    tree = ast.parse(src)
    imports = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    # Check that media import exists (explicit)
    assert any("media" in str(m) for m in imports if m)


def test_domain_import_still_works():
    from bot.package.domain import ImageDescription
    assert ImageDescription(image_src="a", description="b").image_src == "a"

def test_bot_state_is_slim():
    from bot.package.conversation.state import BotState
    hints = BotState.__annotations__.keys()
    for field in ["channel_type","content_kind","has_text","vision_target_count","vision_desc","mentions","llm_text","clean_text"]:
        assert field not in hints, f"BotState should not contain turn field {field}"

def test_turn_input_exists():
    from bot.package.conversation.turn import TurnInput
    t = TurnInput(channel_type=0, bot_id="1", auto_reply=False, content_kind="text",
                  has_text=True, llm_text="hi", clean_text="hi",
                  vision_target_count=0, vision_desc=[], mentions={})
    assert t.llm_text == "hi"
