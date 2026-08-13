"""SatoriMessageIngress：协议事件校验与领域消息归一化。"""

from bot.core.ingress import SatoriMessageIngress
from object.satori import Channel, ChannelType, EventBody, Message, User


def _event(content: str | None = "你好") -> EventBody:
    return EventBody(
        id=1,
        sn=1,
        type="message-created",
        platform="llonebot",
        channel=Channel(id="c1", type=ChannelType.DIRECT),
        user=User(id="u1", name="张三"),
        message=Message(id="m1", content=content),
    )


def test_normalize_generates_event_id_and_trace_id():
    ingress = SatoriMessageIngress(trace_id_factory=lambda: "trace-1")
    msg = ingress.normalize(_event())

    assert msg is not None
    assert msg.event_id == "llonebot:1:m1"
    assert msg.event_type == "message-created"
    assert msg.trace_id == "trace-1"
    assert msg.thread_id == "llonebot::c1"
    assert msg.channel_type == 1
    assert type(msg.channel_type) is int


def test_non_message_event_returns_none():
    ingress = SatoriMessageIngress()
    assert ingress.normalize(EventBody(id=2, sn=2, type="login")) is None


def test_empty_content_returns_none():
    ingress = SatoriMessageIngress()
    assert ingress.normalize(_event(content="   ")) is None


def test_image_message_keeps_domain_fields():
    ingress = SatoriMessageIngress(trace_id_factory=lambda: "trace-img")
    msg = ingress.normalize(_event(content="<img src=\"https://x/1.jpg\"/>"))

    assert msg is not None
    assert msg.content_kind == "image"
    assert msg.has_text is False
    assert msg.llm_text == "[图片]"
    assert msg.image_srcs == ["https://x/1.jpg"]
