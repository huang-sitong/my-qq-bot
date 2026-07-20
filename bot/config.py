from dataclasses import dataclass


@dataclass
class BotConfig:
    ws_url: str = "ws://localhost:5600/v1/events"
    token: str | None = None

    reconnect: bool = True
    max_reconnect_delay: int = 30

    api_base_url: str = "http://localhost:5600"
    api_platform: str = "llonebot"
    api_user_id: str | None = None
