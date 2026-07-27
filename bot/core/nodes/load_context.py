from langchain_core.messages import BaseMessage, SystemMessage

from object.bot.state import BotState


def load_context(state: BotState) -> dict:
    """Inject persona (+ user memories) as SystemMessage and append the new user message."""
    updates: list[BaseMessage] = []
    has_persona = any(isinstance(m, SystemMessage) for m in state["messages"])
    if not has_persona:
        system_content = state["persona"]
        memories = state.get("user_memories", "").strip()
        if memories:
            system_content += f"\n\n关于当前用户已知的信息：\n{memories}"
        updates.append(SystemMessage(content=system_content))
    updates.append(state["new_message"])
    return {"messages": updates}
