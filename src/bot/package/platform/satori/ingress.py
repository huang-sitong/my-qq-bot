"""Satori 协议事件入口：校验并归一化为领域消息。"""

from collections.abc import Callable
from uuid import uuid4

from bot.package.conversation.message import IncomingMessage
from bot.package.platform.satori.content_parser import parse_content
from bot.package.platform.satori.events import EventBody


class SatoriMessageIngress:
    """把 Satori ``EventBody`` 转换为流水线内部的 ``IncomingMessage``。"""

    def __init__(self, trace_id_factory: Callable[[], str] | None = None) -> None:
        self._trace_id_factory = trace_id_factory or (lambda: uuid4().hex)

    def normalize(self, event: EventBody) -> IncomingMessage | None:
        """校验事件并生成 event_id/trace_id；非消息或空内容返回 None。"""
        if event.message is None or event.message.content is None:
            return None
        raw_content = event.message.content
        if not raw_content.strip():
            return None

        platform = event.platform or "unknown"
        guild_id = event.guild.id if event.guild else ""
        channel_id = event.channel.id if event.channel else ""
        user_id = event.user.id if event.user else ""
        user_name = ""
        if event.user:
            user_name = event.user.nick or event.user.name or event.user.id or ""
        thread_id = f"{platform}:{guild_id}:{channel_id}"
        channel_type = int(event.channel.type) if event.channel else 0
        parsed = parse_content(raw_content)
        return IncomingMessage(
            event_id=f"{platform}:{event.id}:{event.message.id}",
            event_type=event.type,
            trace_id=self._trace_id_factory(),
            platform=platform,
            guild_id=guild_id,
            thread_id=thread_id,
            channel_id=channel_id,
            channel_type=channel_type,
            user_id=user_id,
            user_name=user_name,
            raw_content=raw_content,
            content_kind=parsed.kind.value,
            has_text=parsed.has_text,
            llm_text=parsed.llm_text,
            clean_text=parsed.clean_text,
            mentions=parsed.mentions,
            image_srcs=[a.src for a in parsed.attachments if a.type == "img"],
        )
