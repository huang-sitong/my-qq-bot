"""skill_manager — 把 load_skill/unload_skill 的调用结果写回 BotState.active_skills。

工具本身（ToolNode 执行）只返回正文/确认；本节点从消息历史找到调用参数，
决定激活/释放哪些技能。无技能调用或无需变更时返回 {} 不打断工具循环。
"""

import logging

from langchain_core.messages import AIMessage

from domain.bot.state import BotState

logger = logging.getLogger(__name__)


def _last_ai_with_tool_calls(messages) -> AIMessage | None:
    """从末尾向前找最后一个带 tool_calls 的 AIMessage。"""
    for m in reversed(messages):
        if isinstance(m, AIMessage) and getattr(m, "tool_calls", None):
            return m
    return None


async def skill_manager_node(state: BotState, skill_registry=None) -> dict:
    """扫描最近的工具调用，更新 active_skills。"""
    if skill_registry is None:
        return {}
    last_ai = _last_ai_with_tool_calls(state["messages"])
    if last_ai is None:
        return {}
    active = list(state.get("active_skills", []))
    changed = False
    for call in last_ai.tool_calls:
        name = call.get("name")
        args = call.get("args", {}) or {}
        if name == "load_skill":
            skill = args.get("skill_name", "")
            if skill_registry.has(skill) and skill not in active:
                active.append(skill)
                changed = True
        elif name == "unload_skill":
            skill = args.get("skill_name", "")
            if skill in active:
                active.remove(skill)
                changed = True
    if not changed:
        return {}
    logger.info("active_skills updated: %s", active)
    return {"active_skills": active}
