"""用户记忆工具（纯函数）：remember_user_memory / recall_user_memory。

保存/检索当前用户的持久记忆并格式化为文本。
memory_store 与 user_id 由 factory 包装层在调用时注入，LLM 无需知道内部标识。
memory_store 基于 langgraph AsyncSqliteStore（方法全 async），直接 await。
"""


def _format_memories(memories: list[dict]) -> str:
    if not memories:
        return "没有找到相关记忆。"
    return "\n".join(f"- {m['key']}：{m['value']}" for m in memories)


async def remember_user_memory(key: str, value: str, memory_store, user_id: str) -> str:
    """保存一条用户记忆并返回确认文案。"""
    await memory_store.store_memory(user_id, key, value)
    return f"已记住：{key} = {value}"


async def recall_user_memory(keyword: str, memory_store, user_id: str) -> str:
    """检索用户记忆；keyword 为空返回全部，否则按 key/value 子串匹配。"""
    memories = await memory_store.load_memories(user_id)
    keyword = (keyword or "").strip().lower()
    if keyword:
        memories = [
            m for m in memories
            if keyword in m["key"].lower() or keyword in m["value"].lower()
        ]
    return _format_memories(memories)
