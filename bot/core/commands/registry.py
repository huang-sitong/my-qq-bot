"""命令注册表、权限检查和统一执行入口。"""

import logging

from .model import Command, CommandActor, CommandContext, CommandResult

logger = logging.getLogger(__name__)


class CommandRegistry:
    """内存命令注册表，按注册顺序保存。"""

    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}

    def register(self, command: Command) -> None:
        key = command.name.lower()
        if key in self._commands:
            raise ValueError(f"duplicate command: {command.name}")
        self._commands[key] = command

    def resolve(self, name: str) -> Command | None:
        return self._commands.get(name.lower())

    def commands(self) -> list[Command]:
        return list(self._commands.values())


def can_run(command: Command, actor: CommandActor) -> bool:
    """管理员命令只允许 admin actor；everyone 命令对所有人开放。"""
    return command.permission != "admin" or actor.is_admin


async def run_command(command: Command, ctx: CommandContext) -> CommandResult:
    """统一执行：先查权限，再降级 handler 异常。"""
    if not can_run(command, ctx.actor):
        return CommandResult(text="无权执行该指令。")
    try:
        return await command.handler(ctx)
    except Exception:
        logger.exception("Command %s failed for actor %s", command.name, ctx.actor.user_id)
        return CommandResult(text="指令执行失败。")
