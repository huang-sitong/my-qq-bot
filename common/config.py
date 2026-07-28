import os
from dataclasses import dataclass, field


@dataclass
class BotConfig:
    # --- Transport ---
    ws_url: str = "ws://localhost:5600/v1/events"
    token: str | None = None

    reconnect: bool = True
    max_reconnect_delay: int = 30

    api_base_url: str = "http://localhost:5600"
    api_platform: str = "llonebot"
    api_user_id: str | None = None

    # --- Storage ---
    db_dir: str = field(
        default_factory=lambda: os.getenv("BOT_DB_DIR", "db"),
    )

    # --- Persona ---
    persona_prompt: str = field(
        default_factory=lambda: os.getenv("BOT_PERSONA_PROMPT", "你是一个通用的AI助手，请用中文友好地回答问题。"),
    )

    # --- LLM ---
    llm_model: str = field(
        default_factory=lambda: os.getenv("BOT_LLM_MODEL", "sensenova-6.7-flash-lite"),
    )
    llm_temperature: float = field(
        default_factory=lambda: float(os.getenv("BOT_LLM_TEMPERATURE", "0.7")),
    )
    llm_max_retries: int = field(
        default_factory=lambda: int(os.getenv("BOT_LLM_MAX_RETRIES", "1")),
    )
    llm_request_timeout: int = field(
        default_factory=lambda: int(os.getenv("BOT_LLM_REQUEST_TIMEOUT", "30")),
    )
