"""指令模块：图外斜杠指令注册与分发。"""

from .model import Command, CommandActor, CommandContext, CommandResult, CommandServices
from .parser import ParsedCommand, parse_command

__all__ = [
    "Command",
    "CommandActor",
    "CommandContext",
    "CommandResult",
    "CommandServices",
    "ParsedCommand",
    "parse_command",
]
