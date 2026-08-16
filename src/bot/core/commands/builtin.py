"""兼容层：内置命令位于 ``commands.builtin``。"""
from commands.builtin import build_command_registry

__all__ = ["build_command_registry"]
