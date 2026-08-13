# bot/core/skills/__init__.py
"""技能模块：SKILL.md 提示词包（SkillRegistry 加载 + load/unload 工具）。"""

from domain.bot.skill import Skill

from .loader import SkillRegistry
from .tools import load_skill, unload_skill

__all__ = ["Skill", "SkillRegistry", "load_skill", "unload_skill"]
