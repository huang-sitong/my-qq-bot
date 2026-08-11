"""Shared application configuration, prompts, and utilities.

``common`` is the single source of truth for bot settings and prompt
templates — keep environment-specific secrets in ``.env`` instead.
"""

from .config import BotConfig
from .prompts import (
    BASH_TOOL_HINT,
    CURRENT_TIME_HINT,
    DEFAULT_PERSONA_PROMPT,
    MCP_TOOL_HINT,
    MEMORY_TOOL_HINT,
    RETRIEVAL_TASK,
    SKILL_ACTIVE_HINT,
    SKILL_INDEX_HINT,
    SUMMARY_PROMPT,
    VISION_PROMPT,
)

__all__ = [
    "BASH_TOOL_HINT",
    "CURRENT_TIME_HINT",
    "DEFAULT_PERSONA_PROMPT",
    "MCP_TOOL_HINT",
    "MEMORY_TOOL_HINT",
    "RETRIEVAL_TASK",
    "SKILL_ACTIVE_HINT",
    "SKILL_INDEX_HINT",
    "SUMMARY_PROMPT",
    "VISION_PROMPT",
    "BotConfig",
]
