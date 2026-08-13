"""路由领域数据对象。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from domain.bot.command import Command, CommandActor, ParsedCommand


class RouteAction(str, Enum):
    COMMAND = "command"
    REPLY = "reply"
    CONTEXT_ONLY = "context_only"
    SYSTEM = "system"
    MEDIA = "media"
    IGNORE = "ignore"


@dataclass(frozen=True)
class RouteDecision:
    action: RouteAction
    command: Command | None = None
    actor: CommandActor | None = None
    parsed_command: ParsedCommand | None = None
    should_respond: bool = False
    keep_in_context: bool = False
