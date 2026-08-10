"""命令模型：与 Satori / CLI 无关的核心类型。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bot.core.memory import MemoryStore
    from bot.core.rag.service import RagService
    from bot.core.skills import SkillRegistry
    from bot.core.vision.service import VisionService
    from common import BotConfig

CommandHandler = Callable[["CommandContext"], Awaitable["CommandResult"]]


@dataclass(frozen=True)
class CommandActor:
    user_id: str
    name: str
    is_admin: bool
    is_cli: bool = False


@dataclass
class CommandServices:
    version: str
    started_at: float
    bot_name: str
    skill_registry: SkillRegistry | None = None
    rag_service: RagService | None = None
    vision_service: VisionService | None = None
    memory_store: MemoryStore | None = None
    mcp_tool_count: int = 0


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
