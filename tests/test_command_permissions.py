"""权限判定与 run_command 错误降级。"""

import asyncio

from bot.core.commands import (
    Command,
    CommandActor,
    CommandContext,
    CommandResult,
    CommandServices,
    can_run,
    run_command,
)
from common import BotConfig


async def _ok(ctx):
    return CommandResult(text="ok")


async def _boom(ctx):
    raise RuntimeError("boom")


def _ctx(actor):
    return CommandContext(
        raw="/status",
        actor=actor,
        platform="test",
        guild_id="",
        channel_id="ch1",
        thread_id="t1",
        channel_type=1,
        args=(),
        config=BotConfig(_env_file=None),
        services=CommandServices(version="test", started_at=0.0, bot_name=""),
    )


def _command(handler, permission="everyone"):
    return Command(
        name="status",
        description="desc",
        usage="/status",
        permission=permission,
        handler=handler,
    )


def test_everyone_command_allows_non_admin():
    cmd = _command(_ok)
    result = asyncio.run(run_command(cmd, _ctx(CommandActor("u1", "n", False))))
    assert result.text == "ok"


def test_admin_command_denies_non_admin():
    cmd = _command(_ok, permission="admin")
    result = asyncio.run(run_command(cmd, _ctx(CommandActor("u1", "n", False))))
    assert result.text == "无权执行该指令。"


def test_admin_command_allows_admin():
    cmd = _command(_ok, permission="admin")
    result = asyncio.run(run_command(cmd, _ctx(CommandActor("admin", "a", True))))
    assert result.text == "ok"


def test_cli_actor_is_admin():
    # is_cli 视为可信源 → 隐式 admin（不依赖 is_admin 标志）
    cmd = _command(_ok, permission="admin")
    actor = CommandActor("<cli>", "cli", False, is_cli=True)
    result = asyncio.run(run_command(cmd, _ctx(actor)))
    assert result.text == "ok"


def test_handler_returning_str_is_wrapped():
    async def _str_handler(ctx):
        return "plain string"

    cmd = _command(_str_handler)
    result = asyncio.run(run_command(cmd, _ctx(CommandActor("u1", "n", False))))
    assert result.text == "plain string"


def test_unknown_permission_fails_closed():
    cmd = _command(_ok, permission="admins")
    for is_admin in (False, True):
        actor = CommandActor("admin" if is_admin else "u1", "n", is_admin)
        assert not can_run(cmd, actor)
        result = asyncio.run(run_command(cmd, _ctx(actor)))
        assert result.text == "无权执行该指令。"


def test_handler_exception_returns_failure():
    cmd = _command(_boom)
    result = asyncio.run(run_command(cmd, _ctx(CommandActor("u1", "n", False))))
    assert result.text == "指令执行失败。"
