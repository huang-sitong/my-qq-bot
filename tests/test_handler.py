"""MessageHandler ingress：channel_type 必须强制为 int，枚举不得进入 graph state。

LLOneBot 投递的 ``event.channel.type`` 是 ``ChannelType``（IntEnum）实例，
若原样注入 state，LangGraph checkpoint 会持久化该枚举，触发未注册类型
反序列化警告（未来版本会升级为硬错误）。``handler._process`` 在入口用
``int(...)`` 强制转换 —— 本测试锁定该行为，防止回归。
"""

import asyncio

from bot.handler import MessageHandler
from object.satori import Channel, ChannelType, EventBody, Message, User


class _StubGraph:
    """记录 ainvoke 收到的 state，返回空回复。"""

    def __init__(self):
        self.state = None

    async def ainvoke(self, state, config):
        self.state = dict(state)
        return {"reply_text": ""}


class _StubApi:
    async def send_message(self, channel_id, content):
        pass


def _make_handler(graph):
    return MessageHandler(
        client=object(),
        graph=graph,
        persona="你是{bot_name}",
        api_client=_StubApi(),
    )


def _private_event() -> EventBody:
    """模拟 LLOneBot 私聊事件：channel.type 是 ChannelType 枚举（非 int）。"""
    return EventBody(
        id=1,
        sn=1,
        type="message-created",
        platform="llonebot",
        channel=Channel(id="ch1", type=ChannelType.DIRECT),
        user=User(id="u1", name="tester"),
        message=Message(id="m1", content="你好"),
    )


def test_channel_type_coerced_to_int_before_graph():
    graph = _StubGraph()
    handler = _make_handler(graph)
    asyncio.run(handler._process({
        "event": _private_event(),
        "platform": "llonebot",
        "guild_id": "",
        "channel_id": "ch1",
        "user_id": "u1",
        "thread_id": "llonebot::private:ch1",
    }))

    assert graph.state is not None
    ct = graph.state["channel_type"]
    assert ct == 1                                  # 值不变（DIRECT）
    assert type(ct) is int                          # 强制成纯 int
    assert not isinstance(ct, ChannelType)          # 枚举不再进入 state → checkpoint
    assert graph.state["mentions"] == {}   # parse_content → mentions 注入 state（空提及锁键存在）


def test_channel_type_fallback_is_int_when_channel_missing():
    graph = _StubGraph()
    handler = _make_handler(graph)
    event = _private_event()
    event.channel = None  # 无 channel → 走 0 兜底分支
    asyncio.run(handler._process({
        "event": event,
        "platform": "llonebot",
        "guild_id": "",
        "channel_id": "",
        "user_id": "u1",
        "thread_id": "llonebot::guild:",
    }))

    assert graph.state["channel_type"] == 0
    assert type(graph.state["channel_type"]) is int
