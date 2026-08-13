from dataclasses import dataclass


@dataclass(frozen=True)
class IncomingMessage:
    event_id: str
    platform: str
    guild_id: str
    thread_id: str
    channel_id: str
    channel_type: int
    user_id: str
    user_name: str
    raw_content: str
    content_kind: str
    has_text: bool
    llm_text: str
    clean_text: str
    mentions: dict[str, str]
    image_srcs: list[str]
