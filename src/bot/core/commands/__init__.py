"""兼容层：指令上下文已迁移到 ``commands``。"""
from commands import (
    Command,
    CommandActor,
    CommandContext,
    CommandRegistry,
    CommandResult,
    CommandServices,
    ParsedCommand,
    build_command_registry,
    can_run,
    parse_command,
    run_command,
)

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
