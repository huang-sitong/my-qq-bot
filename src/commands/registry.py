"""命令注册表、权限检查和统一执行入口。"""

import logging

from .model import Command, CommandActor, CommandContext, CommandResult

logger = logging.getLogger(__name__)


class CommandRegistry:
    """内存命令注册表，按注册顺序保存。"""

    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}

    def register(self, command: Command) -> None:
        if command.permission not in {"everyone", "admin"}:
            raise ValueError(f"unknown permission: {command.permission}")
        key = command.name.lower()
        if key in self._commands:
            raise ValueError(f"duplicate command: {command.name}")
        self._commands[key] = command

    def resolve(self, name: str) -> Command | None:
        return self._commands.get(name.lower())

    def commands(self) -> list[Command]:
        return list(self._commands.values())


def can_run(command: Command, actor: CommandActor) -> bool:
    """管理员命令只允许 admin actor（CLI actor 视为可信源，隐式 admin）；everyone 命令对所有人开放。"""
    if command.permission == "everyone":
        return True
    return command.permission == "admin" and (actor.is_admin or actor.is_cli)


async def run_command(command: Command, ctx: CommandContext) -> CommandResult:
    """统一执行：先查权限，再降级 handler 异常，并强制返回契约类型。"""
    if not can_run(command, ctx.actor):
        return CommandResult(text="无权执行该指令。")
    try:
        result = await command.handler(ctx)
    except Exception:
        logger.exception("Command %s failed for actor %s", command.name, ctx.actor.user_id)
        return CommandResult(text="指令执行失败。")
    if not isinstance(result, CommandResult):
        # handler 契约是返回 CommandResult；收到裸 str/None 时包装并记 warning，
        # 避免调用方 .text 属性访问崩溃、静默丢回复
        logger.warning(
            "Command %s handler returned %s, wrapping as CommandResult",
            command.name, type(result).__name__,
        )
        return CommandResult(text=str(result) if result is not None else "")
    return result
