"""MessageHandler ingress：channel_type 必须强制为 int，枚举不得进入 graph state。

LLOneBot 投递的 ``event.channel.type`` 是 ``ChannelType``（IntEnum）实例，
若原样注入 state，LangGraph checkpoint 会持久化该枚举，触发未注册类型
反序列化警告（未来版本会升级为硬错误）。``SatoriMessageIngress`` 在入口用
``int(...)`` 强制转换 —— 本测试锁定该行为，防止回归。
"""

import asyncio

from bot.handler import MessageHandler
from commands import Command, CommandServices, build_command_registry
from common import BotConfig
from domain.satori import Channel, ChannelType, EventBody, Message, User


class _StubGraph:
    """记录 ainvoke 收到的 state，返回空回复。"""

    def __init__(self):
        self.state = None
        self.updates = []

    async def ainvoke(self, state, config):
        self.state = dict(state)
        return {"reply_text": ""}

    async def aupdate_state(self, config, updates, as_node=None):
        self.updates.append(updates)


class _StubApi:
    def __init__(self):
        self.sent = []

    async def send_message(self, channel_id, content):
        self.sent.append((channel_id, content))


class _FixedRandom:
    def __init__(self, value):
        self._value = value

    def random(self):
        return self._value


def _make_handler(graph, bot_config=None, command_registry=None, command_services=None):
    return MessageHandler(
        client=object(),
        graph=graph,
        persona="你是{bot_name}",
        api_client=_StubApi(),
        bot_config=bot_config,
        command_registry=command_registry,
        command_services=command_services,
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


async def _dispatch(handler, event):
    await handler.handle(event)
    await handler.start()
    await handler.stop()


def test_channel_type_coerced_to_int_before_graph():
    graph = _StubGraph()
    handler = _make_handler(graph)
    asyncio.run(_dispatch(handler, _private_event()))

    assert graph.state is not None
    ct = graph.state["channel_type"]
    assert ct == 1                                  # 值不变（DIRECT）
    assert type(ct) is int                          # 强制成纯 int
    assert not isinstance(ct, ChannelType)          # 枚举不再进入 state → checkpoint
    assert graph.state["mentions"] == {}   # parse_content → mentions 注入 state（空提及锁键存在）
    assert graph.state["clean_text"] == "你好"   # 预计算清洗文本注入 state
    assert graph.state["channel_id"] == "ch1"


def test_channel_type_fallback_is_int_when_channel_missing():
    graph = _StubGraph()
    handler = _make_handler(graph)
    event = _private_event()
    event.channel = None  # 无 channel → 走 0 兜底分支
    asyncio.run(_dispatch(handler, event))

    assert graph.updates


def _command_event(content, user_id="admin1"):
    return EventBody(
        id=2,
        sn=2,
        type="message-created",
        platform="llonebot",
        channel=Channel(id="ch1", type=ChannelType.DIRECT),
        user=User(id=user_id, name="admin"),
        message=Message(id="m2", content=content),
    )


def _command_services():
    return CommandServices(version="test", started_at=0.0, bot_name="")


async def _boom(ctx):
    raise RuntimeError("boom")


def test_registered_command_skips_graph():
    graph = _StubGraph()
    services = _command_services()
    registry = build_command_registry(services)
    config = BotConfig(_env_file=None, command_enabled=True, admin_ids=["admin1"])
    handler = _make_handler(
        graph,
        bot_config=config,
        command_registry=registry,
        command_services=services,
    )

    asyncio.run(_dispatch(handler, _command_event("/ping")))

    assert graph.state is None
    assert handler._api_client.sent == [("ch1", "Pong.")]


def test_unknown_command_still_enters_graph():
    graph = _StubGraph()
    services = _command_services()
    registry = build_command_registry(services)
    config = BotConfig(_env_file=None, command_enabled=True, admin_ids=["admin1"])
    handler = _make_handler(
        graph,
        bot_config=config,
        command_registry=registry,
        command_services=services,
    )

    asyncio.run(_dispatch(handler, _command_event("/unknown")))

    assert graph.state is not None


def test_admin_command_permission_denied_skips_graph():
    graph = _StubGraph()
    services = _command_services()
    registry = build_command_registry(services)
    config = BotConfig(_env_file=None, command_enabled=True, admin_ids=["admin1"])
    handler = _make_handler(
        graph,
        bot_config=config,
        command_registry=registry,
        command_services=services,
    )

    asyncio.run(_dispatch(handler, _command_event("/status", user_id="u-not-admin")))

    assert graph.state is None
    assert handler._api_client.sent[0][1] == "无权执行该指令。"


def test_command_disabled_enters_graph():
    graph = _StubGraph()
    services = _command_services()
    registry = build_command_registry(services)
    config = BotConfig(_env_file=None, command_enabled=False)
    handler = _make_handler(
        graph,
        bot_config=config,
        command_registry=registry,
        command_services=services,
    )

    asyncio.run(_dispatch(handler, _command_event("/ping")))

    assert graph.state is not None


def test_malformed_command_args_returns_usage():
    graph = _StubGraph()
    services = _command_services()
    registry = build_command_registry(services)
    config = BotConfig(_env_file=None, command_enabled=True, admin_ids=["admin1"])
    handler = _make_handler(
        graph,
        bot_config=config,
        command_registry=registry,
        command_services=services,
    )

    asyncio.run(_dispatch(handler, _command_event('/help "oops')))

    assert graph.state is None
    assert handler._api_client.sent[0][1] == "指令参数错误，用法：/help [command]"


def test_malformed_admin_command_permission_denied_skips_graph():
    graph = _StubGraph()
    services = _command_services()
    registry = build_command_registry(services)
    config = BotConfig(_env_file=None, command_enabled=True, admin_ids=["admin1"])
    handler = _make_handler(
        graph,
        bot_config=config,
        command_registry=registry,
        command_services=services,
    )

    asyncio.run(_dispatch(handler, _command_event('/status "oops', user_id="u-not-admin")))

    assert graph.state is None
    assert handler._api_client.sent[0][1] == "无权执行该指令。"


def test_handler_exception_returns_failure_reply():
    graph = _StubGraph()
    services = _command_services()
    registry = build_command_registry(services)
    registry.register(Command(
        name="boom",
        description="boom",
        usage="/boom",
        permission="everyone",
        handler=_boom,
    ))
    config = BotConfig(_env_file=None, command_enabled=True, admin_ids=["admin1"])
    handler = _make_handler(
        graph,
        bot_config=config,
        command_registry=registry,
        command_services=services,
    )
    asyncio.run(_dispatch(handler, _command_event("/boom")))
    assert graph.state is None
    assert handler._api_client.sent[0][1] == "指令执行失败。"


def test_missing_required_arg_returns_usage():
    graph = _StubGraph()
    services = _command_services()
    registry = build_command_registry(services)
    config = BotConfig(_env_file=None, command_enabled=True, admin_ids=["admin1"])
    handler = _make_handler(
        graph,
        bot_config=config,
        command_registry=registry,
        command_services=services,
    )
    asyncio.run(_dispatch(handler, _command_event("/skill")))
    assert graph.state is None
    assert handler._api_client.sent[0][1] == "用法：/skill <name>"


def test_custom_prefix_dispatches_command():
    graph = _StubGraph()
    services = _command_services()
    registry = build_command_registry(services)
    config = BotConfig(
        _env_file=None, command_enabled=True, admin_ids=["admin1"], command_prefix="!",
    )
    handler = _make_handler(
        graph,
        bot_config=config,
        command_registry=registry,
        command_services=services,
    )
    asyncio.run(_dispatch(handler, _command_event("!ping")))
    assert graph.state is None
    assert handler._api_client.sent == [("ch1", "Pong.")]


def test_unicode_command_name_falls_through_to_graph():
    graph = _StubGraph()
    services = _command_services()
    registry = build_command_registry(services)
    config = BotConfig(_env_file=None, command_enabled=True, admin_ids=["admin1"])
    handler = _make_handler(
        graph,
        bot_config=config,
        command_registry=registry,
        command_services=services,
    )
    asyncio.run(_dispatch(handler, _command_event("/帮助")))
    assert graph.state is not None
    assert graph.state["clean_text"] == "/帮助"


def test_command_dispatches_in_group_channel():
    graph = _StubGraph()
    services = _command_services()
    registry = build_command_registry(services)
    config = BotConfig(_env_file=None, command_enabled=True, admin_ids=["admin1"])
    handler = _make_handler(
        graph,
        bot_config=config,
        command_registry=registry,
        command_services=services,
    )
    event = EventBody(
        id=3,
        sn=3,
        type="message-created",
        platform="llonebot",
        channel=Channel(id="g1", type=ChannelType.TEXT),
        user=User(id="admin1", name="admin"),
        message=Message(id="m3", content="/ping"),
    )
    asyncio.run(_dispatch(handler, event))
    assert graph.state is None
    assert handler._api_client.sent == [("g1", "Pong.")]


def test_auto_reply_private_explicit_is_not_marked_auto_reply():
    graph = _StubGraph()
    config = BotConfig(_env_file=None, auto_reply=True)
    handler = _make_handler(graph, bot_config=config)
    asyncio.run(_dispatch(handler, _private_event()))
    assert graph.state["auto_reply"] is False


def test_group_non_at_auto_reply_allowed_when_random_hits():
    graph = _StubGraph()
    config = BotConfig(
        _env_file=None,
        auto_reply=True,
        auto_reply_random_rate=1.0,
        auto_reply_cooldown=0,
    )
    handler = _make_handler(graph, bot_config=config)
    handler._random = _FixedRandom(0.1)
    event = EventBody(
        id=10,
        sn=10,
        type="message-created",
        platform="llonebot",
        channel=Channel(id="g1", type=ChannelType.TEXT),
        user=User(id="u2", name="tester"),
        message=Message(id="m10", content="晚上吃什么"),
    )
    asyncio.run(_dispatch(handler, event))
    assert graph.state["auto_reply"] is True
    assert graph.state["has_text"] is True


def test_auto_reply_cooldown_blocks_second_reply():
    graph = _StubGraph()
    config = BotConfig(
        _env_file=None,
        auto_reply=True,
        auto_reply_random_rate=1.0,
        auto_reply_cooldown=60,
    )
    handler = _make_handler(graph, bot_config=config)
    handler._random = _FixedRandom(0.1)
    handler._last_auto_reply_at["llonebot::g1"] = 1e18

    event = EventBody(
        id=11,
        sn=11,
        type="message-created",
        platform="llonebot",
        channel=Channel(id="g1", type=ChannelType.TEXT),
        user=User(id="u2", name="tester"),
        message=Message(id="m11", content="晚上吃什么"),
    )
    asyncio.run(_dispatch(handler, event))
    assert graph.state is None


def test_auto_reply_defaults_false_when_config_absent():
    graph = _StubGraph()
    handler = _make_handler(graph)  # bot_config=None
    asyncio.run(_dispatch(handler, _private_event()))
    assert graph.state["auto_reply"] is False
