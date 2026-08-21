"""技能上下文工厂。"""

from __future__ import annotations

import logging

from bot.package.config import BotConfig

from .loader import SkillRegistry

logger = logging.getLogger(__name__)


def create_skill_registry(config: BotConfig) -> SkillRegistry | None:
    """按配置扫描 skills 目录；禁用时返回 None，目录缺失返回空注册表。"""
    if not getattr(config, "skills_enabled", True):
        return None
    registry = SkillRegistry.from_directory(config.skills_dir, index_max=config.skills_index_max)
    logger.info("Loaded %d skills from %s", registry.total, config.skills_dir)
    return registry
