"""指令模块：图外斜杠指令注册与分发。"""

from object.bot.command import (
    Command,
    CommandActor,
    CommandContext,
    CommandResult,
    CommandServices,
    ParsedCommand,
)

from .builtin import build_command_registry
from .parser import parse_command
from .registry import CommandRegistry, can_run, run_command

__all__ = [
    "Command",
    "CommandActor",
    "CommandContext",
    "CommandRegistry",
    "CommandResult",
    "CommandServices",
    "ParsedCommand",
    "build_command_registry",
    "can_run",
    "parse_command",
    "run_command",
]
