"""技能提示词 — 归属 skill 上下文，单一源。

供 orchestration/call_llm 与 utils/context 参数注入使用，避免 utils 依赖 orchestration。
"""

SKILL_INDEX_HINT = "可用技能（按需用 load_skill 加载正文）："
SKILL_ACTIVE_HINT = "当前已激活技能（遵循其规则）："

__all__ = ["SKILL_ACTIVE_HINT", "SKILL_INDEX_HINT"]
