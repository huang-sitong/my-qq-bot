from bot.package.commands import Command, CommandRegistry
from bot.package.conversation.message import IncomingMessage
from bot.package.pipeline.router import RouteAction, route_incoming


async def _ping(ctx):
    return "Pong."


def _message(**overrides):
    data = {
        "event_id": "e1",
        "platform": "llonebot",
        "guild_id": "",
        "thread_id": "llonebot::c1",
        "channel_id": "c1",
        "channel_type": 1,
        "user_id": "u1",
        "user_name": "张三",
        "raw_content": "/ping",
        "content_kind": "text",
        "has_text": True,
        "llm_text": "/ping",
        "clean_text": "/ping",
        "mentions": {},
        "image_srcs": [],
    }
    data.update(overrides)
    return IncomingMessage(**data)


def _registry():
    registry = CommandRegistry()
    registry.register(Command(
        name="ping",
        description="ping",
        usage="/ping",
        permission="everyone",
        handler=_ping,
    ))
    return registry


def test_command_route():
    decision = route_incoming(
        _message(),
        command_registry=_registry(),
        command_enabled=True,
        command_prefix="/",
    )
    assert decision.action == RouteAction.COMMAND
    assert decision.command.name == "ping"
    assert decision.parsed_command.args == ()


def test_unknown_command_falls_through_to_reply_in_private():
    decision = route_incoming(
        _message(raw_content="/unknown", clean_text="/unknown", llm_text="/unknown"),
        command_registry=_registry(),
        command_enabled=True,
        command_prefix="/",
        bot_id="bot1",
        bot_name="小助手",
    )
    assert decision.action == RouteAction.REPLY


def test_group_non_mention_text_is_context_only():
    decision = route_incoming(
        _message(
            guild_id="g1",
            thread_id="llonebot:g1:c1",
            channel_id="c1",
            channel_type=0,
            raw_content="晚上吃什么",
            clean_text="晚上吃什么",
            llm_text="晚上吃什么",
        ),
        auto_reply_allowed=False,
    )
    assert decision.action == RouteAction.CONTEXT_ONLY
    assert decision.keep_in_context is True


def test_group_auto_reply_is_reply():
    decision = route_incoming(
        _message(
            guild_id="g1",
            thread_id="llonebot:g1:c1",
            channel_type=0,
            raw_content="晚上吃什么",
            clean_text="晚上吃什么",
            llm_text="晚上吃什么",
        ),
        auto_reply_allowed=True,
    )
    assert decision.action == RouteAction.REPLY


def test_pure_media_routes_to_media_pipeline():
    decision = route_incoming(
        _message(
            channel_type=0,
            raw_content="<img src=\"https://x/1.jpg\"/>",
            content_kind="image",
            has_text=False,
            llm_text="[图片]",
            clean_text="",
            image_srcs=["https://x/1.jpg"],
        ),
        auto_reply_allowed=False,
    )
    assert decision.action == RouteAction.MEDIA


def test_non_conversation_event_routes_to_system_pipeline():
    decision = route_incoming(
        _message(event_type="login"),
        command_registry=_registry(),
        command_enabled=True,
    )
    assert decision.action == RouteAction.SYSTEM
