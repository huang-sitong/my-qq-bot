"""命令层领域数据对象。

这里只存放数据结构和命令 handler 的类型契约；解析、注册、权限、执行逻辑
仍由 ``commands`` 负责。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bot.package.commands.services import CommandServices
    from bot.package.config import BotConfig

__all__ = [
    "Command",
    "CommandActor",
    "CommandContext",
    "CommandHandler",
    "CommandResult",
    "ParsedCommand",
]

CommandHandler = Callable[["CommandContext"], Awaitable["CommandResult"]]


@dataclass(frozen=True)
class CommandActor:
    user_id: str
    name: str
    is_admin: bool
    is_cli: bool = False


@dataclass(frozen=True)
class CommandContext:
    raw: str
    actor: CommandActor
    platform: str
    guild_id: str
    channel_id: str
    thread_id: str
    channel_type: int
    args: tuple[str, ...]
    config: BotConfig
    services: CommandServices


@dataclass(frozen=True)
class CommandResult:
    text: str
    data: dict | None = None


@dataclass(frozen=True)
class Command:
    name: str
    description: str
    usage: str
    permission: str
    handler: CommandHandler


@dataclass(frozen=True)
class ParsedCommand:
    name: str
    args: tuple[str, ...] = ()
    error: str | None = None
