from object.bot.state import BotState


def load_context(state: BotState) -> dict:
    """Append the new user message to conversation history.

    Persona injection has been moved to ``call_llm_node``, where the
    SystemMessage is built dynamically before each LLM call and never
    persisted to checkpoint.
    """
    return {"messages": [state["new_message"]]}
