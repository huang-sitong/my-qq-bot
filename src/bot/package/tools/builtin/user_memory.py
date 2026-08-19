"""用户记忆工具（纯函数）：remember_user_memory / recall_user_memory。

保存/检索目标用户的持久记忆并格式化为文本。
memory_store 与最终 user_id 由 factory 包装层解析注入；LLM 通过显式
user_name/user_id 参数指定目标用户，缺失时回退最近 HumanMessage。
memory_store 基于 langgraph AsyncSqliteStore（方法全 async），直接 await。
"""

from langchain_core.messages import BaseMessage, HumanMessage

from bot.package.utils import speaker_from_messages


def resolve_memory_user_id(
    user_id: str = "",
    user_name: str = "",
    messages: list[BaseMessage] | None = None,
) -> tuple[str, str]:
    """解析记忆操作的目标用户，返回 ``(user_id, error)``。

    ``user_id`` 显式提供时优先使用；否则按 ``user_name`` 匹配最近 HumanMessage
    的显示名；两者都为空时回退到最近一条 HumanMessage。
    """
    if user_id:
        return user_id, ""
    if user_name:
        target = user_name.strip()
        for message in reversed(messages or []):
            if not isinstance(message, HumanMessage):
                continue
            kwargs = message.additional_kwargs or {}
            message_name = (
                message.name
                or str(kwargs.get("user_name", ""))
                or ""
            )
            if message_name == target:
                found_id = str(kwargs.get("user_id", ""))
                if found_id:
                    return found_id, ""
        return "", f"找不到名为 {user_name} 的发言者"
    last_id, _ = speaker_from_messages(messages)
    if not last_id:
        return "", "无法确定用户，请提供 user_id 或 user_name"
    return last_id, ""


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
