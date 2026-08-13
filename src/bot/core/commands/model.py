"""命令模型兼容导出。

数据对象已统一到 ``domain/bot/command.py``，本模块保留旧导入路径。
"""

from domain.bot.command import (
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
