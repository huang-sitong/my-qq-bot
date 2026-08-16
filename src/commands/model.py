"""命令模型兼容导出。

领域数据对象在 ``domain/bot/command.py``；``CommandServices`` 属于应用服务层，
实现在 ``commands.services``。本模块保留旧导入路径。
"""

from .domain import (
    Command,
    CommandActor,
    CommandContext,
    CommandHandler,
    CommandResult,
)
from .services import CommandServices

__all__ = [
    "Command",
    "CommandActor",
    "CommandContext",
    "CommandHandler",
    "CommandResult",
    "CommandServices",
]
