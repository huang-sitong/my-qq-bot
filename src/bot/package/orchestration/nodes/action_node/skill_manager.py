"""skill_manager — 把 load_skill/unload_skill 的调用结果写回 BotState.active_skills。

工具本身（ToolNode 执行）只返回正文/确认；本节点从消息历史找到调用参数，
决定激活/释放哪些技能。无技能调用或无需变更时返回 {} 不打断工具循环。
"""

import logging

from langchain_core.messages import AIMessage, ToolMessage

from bot.package.conversation.state import BotState
from bot.package.utils import format_message_for_log

logger = logging.getLogger(__name__)


def _last_ai_with_tool_calls(messages) -> AIMessage | None:
    """从末尾向前找最后一个带 tool_calls 的 AIMessage。"""
    for m in reversed(messages):
        if isinstance(m, AIMessage) and getattr(m, "tool_calls", None):
            return m
    return None


async def skill_manager_node(state: BotState, skill_registry=None) -> dict:
    """扫描最近的工具调用，更新 active_skills。"""
    last_ai = _last_ai_with_tool_calls(state["messages"])
    if last_ai is not None:
        # 把本轮工具执行产生的 ToolMessage 写入日志，方便在 ./log 中查看工具回传内容。
        # 优先按 tool_call_id 精确匹配；没有 ID 时退回记录最近一次带 tool_calls 的
        # AIMessage 之后的所有 ToolMessage。
        messages = state.get("messages", [])
        tool_call_ids = {
            call.get("id")
            for call in last_ai.tool_calls
            if isinstance(call, dict) and call.get("id")
        }
        if tool_call_ids:
            for msg in messages:
                if (
                    isinstance(msg, ToolMessage)
                    and getattr(msg, "tool_call_id", None) in tool_call_ids
                ):
                    logger.info(
                        "Context message tool_result thread=%s: %s",
                        state.get("thread_id", ""),
                        format_message_for_log(msg),
                    )
        else:
            start = -1
            for index, msg in enumerate(messages):
                if msg is last_ai:
                    start = index
                    break
            for msg in messages[start + 1:]:
                if isinstance(msg, ToolMessage):
                    logger.info(
                        "Context message tool_result thread=%s: %s",
                        state.get("thread_id", ""),
                        format_message_for_log(msg),
                    )
    if last_ai is None:
        return {}
    if skill_registry is None:
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
