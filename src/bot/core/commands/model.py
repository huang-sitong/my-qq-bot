"""兼容层：指令模型位于 ``commands.model``。"""
from commands.model import (
    Command,
    CommandActor,
    CommandContext,
    CommandHandler,
    CommandResult,
    CommandServices,
)

__all__ = [
    "Command",
    "CommandActor",
    "CommandContext",
    "CommandHandler",
    "CommandResult",
    "CommandServices",
]
