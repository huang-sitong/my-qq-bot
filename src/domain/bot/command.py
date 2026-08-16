"""兼容层：命令领域模型已迁移到 ``commands.domain``。"""
from commands.domain import (
    Command,
    CommandActor,
    CommandContext,
    CommandHandler,
    CommandResult,
    CommandServices,
    ParsedCommand,
)

__all__ = [
    "Command",
    "CommandActor",
    "CommandContext",
    "CommandHandler",
    "CommandResult",
    "CommandServices",
    "ParsedCommand",
]
