"""斜杠指令纯解析器：prefix + 命令名 + 位置参数。"""

import re
import shlex

from .domain import ParsedCommand

# 命令名须以字母开头（避免 /123、/-- 被误当作命令名；非法名直接回落对话流程）
_NAME_RE = re.compile(r"[a-z][a-z0-9_-]*")
def parse_command(text: str, prefix: str = "/") -> ParsedCommand | None:
    """解析 ``prefix + name + args``；未命中或不合法命令名返回 None。"""
    if not prefix or not text.startswith(prefix):
        return None
    remainder = text[len(prefix):].strip()
    if not remainder:
        return None
    name = remainder.split(None, 1)[0].lower()
    if not _NAME_RE.fullmatch(name):
        return None
    raw_args = remainder[len(name):].strip()
    if not raw_args:
        return ParsedCommand(name=name)
    try:
        args = tuple(shlex.split(raw_args))
    except ValueError as exc:
        return ParsedCommand(name=name, error=str(exc))
    return ParsedCommand(name=name, args=args)
