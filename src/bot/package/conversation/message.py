from dataclasses import dataclass

from .record import MessageRecord, MessageRole


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
    event_type: str = ""
    trace_id: str = ""

    def to_record(
        self,
        *,
        role: MessageRole = "user",
        created_at: str = "",
    ) -> MessageRecord:
        """转换为框架无关的纯领域消息记录。"""
        return MessageRecord.from_incoming(
            self,
            role=role,
            created_at=created_at,
        )
