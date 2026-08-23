"""技能上下文工厂。"""

from __future__ import annotations

import logging

from bot.package.config import BotConfig

from .domain import SkillSelection
from .loader import SkillRegistry

logger = logging.getLogger(__name__)


def create_skill_registry(config: BotConfig) -> SkillRegistry | None:
    """按配置扫描 skills 目录；禁用时返回 None，目录缺失返回空注册表。"""
    if not getattr(config, "skills_enabled", True):
        return None
    registry = SkillRegistry.from_directory(config.skills_dir, index_max=config.skills_index_max)
    selection = SkillSelection.from_lists(
        getattr(config, "skills_allowlist", []),
        getattr(config, "skills_denylist", []),
    )
    available_names = set(registry.names())
    configured_names = set(selection.allowlist) | set(selection.denylist)
    unknown = sorted(configured_names - available_names)
    if unknown:
        logger.warning("Skill selection contains unknown names ignored: %s", ", ".join(unknown))
    registry = registry.restrict(selection)
    logger.info(
        "Loaded %d skills from %s, selected %d",
        len(available_names), config.skills_dir, registry.total,
    )
    return registry
