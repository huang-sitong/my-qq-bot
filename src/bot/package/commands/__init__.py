"""指令模块：图外斜杠指令注册与分发。

显式导出，无懒加载魔法。
"""

from .builtin import build_command_registry
from .domain import (
    Command,
    CommandActor,
    CommandContext,
    CommandHandler,
    CommandResult,
    ParsedCommand,
)
from .parser import parse_command
from .registry import CommandRegistry, can_run, run_command
from .services import CommandServices

__all__ = [
    "Command",
    "CommandActor",
    "CommandContext",
    "CommandHandler",
    "CommandRegistry",
    "CommandResult",
    "CommandServices",
    "ParsedCommand",
    "build_command_registry",
    "can_run",
    "parse_command",
    "run_command",
]
