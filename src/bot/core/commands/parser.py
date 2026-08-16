"""兼容层：指令解析器位于 ``commands.parser``。"""
from commands.parser import ParsedCommand, parse_command

__all__ = ["ParsedCommand", "parse_command"]
