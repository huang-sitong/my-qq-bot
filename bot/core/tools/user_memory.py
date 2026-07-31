"""用户记忆工具：remember_user_memory / recall_user_memory。

纯函数：保存/检索当前用户的持久记忆并格式化为文本。
memory_store 与 user_id 由 tool_node 在调用时注入，LLM 无需知道内部标识。
"""

import asyncio

TOOL_NAME_REMEMBER = "remember_user_memory"
TOOL_NAME_RECALL = "recall_user_memory"

TOOL_SCHEMA_REMEMBER = {
    "type": "function",
    "function": {
        "name": TOOL_NAME_REMEMBER,
        "description": "保存当前用户的持久性个人信息（名字、偏好、习惯、背景等）。"
        "当用户提到新的持久事实时调用；更新已有记忆时直接以相同 key 覆盖。",
        "parameters": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "记忆的语义标签，如 \"喜欢的食物\""},
                "value": {"type": "string", "description": "记忆内容，中文表述"},
            },
            "required": ["key", "value"],
        },
    },
}

TOOL_SCHEMA_RECALL = {
    "type": "function",
    "function": {
        "name": TOOL_NAME_RECALL,
        "description": "检索当前用户的持久记忆（名字、偏好、习惯、背景等）。"
        "当需要用户的个人信息、或回想之前提到过的用户事实时使用。"
        "keyword 留空返回全部记忆，否则按 key/value 模糊匹配。",
        "parameters": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "检索关键词，按 key/value 模糊匹配；留空返回全部记忆",
                },
            },
            "required": ["keyword"],
        },
    },
}


def _format_memories(memories: list[dict]) -> str:
    if not memories:
        return "没有找到相关记忆。"
    return "\n".join(f"- {m['key']}：{m['value']}" for m in memories)


async def remember_user_memory(key: str, value: str, memory_store, user_id: str) -> str:
    """保存一条用户记忆并返回确认文案。"""
    await asyncio.to_thread(memory_store.store_memory, user_id, key, value)
    return f"已记住：{key} = {value}"


async def recall_user_memory(keyword: str, memory_store, user_id: str) -> str:
    """检索用户记忆；keyword 为空返回全部，否则按 key/value 子串匹配。"""
    memories = await asyncio.to_thread(memory_store.load_memories, user_id)
    keyword = (keyword or "").strip().lower()
    if keyword:
        memories = [
            m for m in memories
            if keyword in m["key"].lower() or keyword in m["value"].lower()
        ]
    return _format_memories(memories)
