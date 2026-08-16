"""兼容层：技能上下文已迁移到 ``skill``。"""
from skill import Skill, SkillRegistry, load_skill, unload_skill

__all__ = ["Skill", "SkillRegistry", "load_skill", "unload_skill"]
