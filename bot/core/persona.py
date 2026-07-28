from bot.core.prompts import DEFAULT_PERSONA_PROMPT


def load_persona(persona_prompt: str = "") -> str:
    """Return the persona prompt.

    Uses *persona_prompt* if provided (e.g. from ``BotConfig`` or ``BOT_PERSONA_PROMPT``
    env var), otherwise falls back to the built-in default.
    """
    if persona_prompt and persona_prompt.strip():
        return persona_prompt
    return DEFAULT_PERSONA_PROMPT
