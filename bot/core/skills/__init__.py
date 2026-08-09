# bot/core/skills/__init__.py
"""技能模块：SKILL.md 提示词包（SkillRegistry 加载 + load/unload 工具）。"""

from .loader import Skill, SkillRegistry

__all__ = ["Skill", "SkillRegistry"]
