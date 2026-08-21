"""记忆上下文工厂。"""

from __future__ import annotations

from bot.package.config import BotConfig


def create_memory_store(config: BotConfig):
    """创建 MemoryStore（无开关，始终可用）。"""
    from bot.package.memory import MemoryStore

    return MemoryStore(db_dir=config.db_dir)
