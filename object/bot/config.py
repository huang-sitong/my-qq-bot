import os
from dataclasses import dataclass, field


@dataclass
class BotConfig:
    ws_url: str = "ws://localhost:5600/v1/events"
    token: str | None = None

    reconnect: bool = True
    max_reconnect_delay: int = 30

    api_base_url: str = "http://localhost:5600"
    api_platform: str = "llonebot"
    api_user_id: str | None = None

    db_dir: str = field(
        default_factory=lambda: os.getenv("BOT_DB_DIR", "db"),
    )
