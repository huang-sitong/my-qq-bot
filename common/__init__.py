"""Shared application configuration, prompts, and utilities.

``common`` is the single source of truth for bot settings and prompt
templates — keep environment-specific secrets in ``.env`` instead.
"""

from .config import BotConfig
from .prompts import (
    CURRENT_TIME_HINT,
    DEFAULT_PERSONA_PROMPT,
    MEMORY_TOOL_HINT,
    RETRIEVAL_TASK,
    ROUTER_PROMPT,
    SUMMARY_PROMPT,
    VISION_PROMPT,
)

__all__ = [
    "BotConfig",
    "CURRENT_TIME_HINT",
    "DEFAULT_PERSONA_PROMPT",
    "MEMORY_TOOL_HINT",
    "RETRIEVAL_TASK",
    "ROUTER_PROMPT",
    "SUMMARY_PROMPT",
    "VISION_PROMPT",
]
