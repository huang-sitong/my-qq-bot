# skill/tools.py
"""load_skill / unload_skill 纯函数。

只返回正文/确认文本，不写任何状态——激活状态的写回由 skill_manager 节点
从 state 完成（工具经 ToolNode 返回 ToolMessage 后由节点消费）。
"""

async def load_skill(skill_name: str, skill_registry) -> str:
    """返回技能正文；不存在/未启用给出可纠正提示。"""
    if skill_registry is None:
        return "技能功能未启用。"
    body = skill_registry.get_body(skill_name)
    if body is None:
        available = ", ".join(skill_registry.names())
        return f"技能 '{skill_name}' 不存在。可用技能：{available or '（无）'}"
    return f"已加载技能 '{skill_name}'，正文：\n{body}"


async def unload_skill(skill_name: str) -> str:
    """返回停用确认（幂等）。"""
    return f"技能 '{skill_name}' 已停用。"
