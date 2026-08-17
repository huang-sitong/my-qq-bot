"""指令模块：图外斜杠指令注册与分发。

采用懒加载导出，避免在导入 ``commands`` 时立即加载 ``builtin`` 等依赖
``context``/``orchestration`` 的模块，从而打破 ``commands -> bot -> commands`` 的循环导入。
"""

from __future__ import annotations

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

_module_map = {
    "Command": "domain",
    "CommandActor": "domain",
    "CommandContext": "domain",
    "CommandHandler": "domain",
    "CommandResult": "domain",
    "ParsedCommand": "domain",
    "CommandServices": "services",
    "CommandRegistry": "registry",
    "can_run": "registry",
    "run_command": "registry",
    "parse_command": "parser",
    "build_command_registry": "builtin",
}


def __getattr__(name: str):
    module_name = _module_map.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module = importlib.import_module(f".{module_name}", __package__)
    return getattr(module, name)


def __dir__() -> list[str]:
    return sorted(__all__)
