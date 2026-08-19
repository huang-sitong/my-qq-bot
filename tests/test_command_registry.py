"""CommandRegistry 注册、解析和稳定顺序。"""

import pytest

from bot.package.commands import Command, CommandRegistry, CommandResult


async def _ok(ctx):
    return CommandResult(text="ok")


def _cmd(name="ping", permission="everyone"):
    return Command(
        name=name,
        description="desc",
        usage=f"/{name}",
        permission=permission,
        handler=_ok,
    )


def test_register_and_resolve():
    reg = CommandRegistry()
    reg.register(_cmd("ping"))
    assert reg.resolve("PING").name == "ping"
    assert reg.resolve("missing") is None


def test_duplicate_name_raises():
    reg = CommandRegistry()
    reg.register(_cmd("ping"))
    with pytest.raises(ValueError):
        reg.register(_cmd("PING"))


def test_unknown_permission_raises():
    reg = CommandRegistry()
    with pytest.raises(ValueError):
        reg.register(_cmd("status", permission="admins"))


def test_commands_preserve_registration_order():
    reg = CommandRegistry()
    reg.register(_cmd("ping"))
    reg.register(_cmd("help"))
    assert [c.name for c in reg.commands()] == ["ping", "help"]
