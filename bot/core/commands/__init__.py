"""指令模块：图外斜杠指令注册与分发。"""

from .model import Command, CommandActor, CommandContext, CommandResult, CommandServices
from .parser import ParsedCommand, parse_command
from .registry import CommandRegistry, can_run, run_command

__all__ = [
    "Command",
    "CommandActor",
    "CommandContext",
    "CommandRegistry",
    "CommandResult",
    "CommandServices",
    "ParsedCommand",
    "can_run",
    "parse_command",
    "run_command",
]
