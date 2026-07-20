import logging
import tomllib
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_PERSONA = "你是一个通用的AI助手，请用中文友好地回答问题。"


@lru_cache
def load_persona() -> str:
    """Load persona system prompt from ``pyproject.toml`` ``[tool.bot].persona_prompt``.

    Falls back to a default universal-assistant prompt.
    The prompt is cached after first load.
    """
    pyproject = Path("pyproject.toml")
    if not pyproject.exists():
        logger.warning("pyproject.toml not found, using default persona")
        return _DEFAULT_PERSONA

    raw = pyproject.read_text(encoding="utf-8")
    config = tomllib.loads(raw)
    try:
        prompt = config["tool"]["bot"]["persona_prompt"]
    except KeyError:
        logger.warning("[tool.bot].persona_prompt not found, using default persona")
        return _DEFAULT_PERSONA

    if not isinstance(prompt, str) or not prompt.strip():
        logger.warning("persona_prompt is empty, using default persona")
        return _DEFAULT_PERSONA

    logger.info("Persona loaded (%d characters)", len(prompt))
    return prompt
