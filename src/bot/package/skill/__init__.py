# skill/__init__.py
"""技能模块：SKILL.md 提示词包（SkillRegistry 加载 + load/unload 工具）。"""

from .domain import Skill
from .factory import create_skill_registry
from .loader import SkillRegistry
from .prompts import SKILL_ACTIVE_HINT, SKILL_INDEX_HINT
from .tools import load_skill, unload_skill

__all__ = ["SKILL_ACTIVE_HINT", "SKILL_INDEX_HINT", "Skill", "SkillRegistry", "create_skill_registry", "load_skill", "unload_skill"]
