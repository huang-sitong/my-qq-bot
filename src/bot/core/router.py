from bot.core.utils.routing import decide_reply, keep_in_context
from commands import CommandActor, CommandRegistry, parse_command
from conversation.content import MessageKind
from conversation.message import IncomingMessage
from conversation.router import RouteAction, RouteDecision

_CONVERSATION_EVENT_TYPES = {"message-created"}
_MEDIA_KINDS = {
    MessageKind.IMAGE.value,
    MessageKind.FILE.value,
    MessageKind.AUDIO.value,
    MessageKind.VIDEO.value,
}


def route_incoming(
    message: IncomingMessage,
    *,
    command_registry: CommandRegistry | None = None,
    command_enabled: bool = False,
    command_prefix: str = "/",
    bot_id: str = "",
    bot_name: str = "",
    auto_reply_allowed: bool = False,
    admin_ids: tuple[str, ...] = (),
) -> RouteDecision:
    if message.event_type and message.event_type not in _CONVERSATION_EVENT_TYPES:
        return RouteDecision(action=RouteAction.SYSTEM)

    if (
        command_enabled
        and command_registry is not None
        and message.content_kind == "text"
    ):
        parsed_command = parse_command(message.clean_text, command_prefix)
        if parsed_command is not None:
            command = command_registry.resolve(parsed_command.name)
            if command is not None:
                actor = CommandActor(
                    user_id=message.user_id,
                    name=message.user_name,
                    is_admin=message.user_id in admin_ids,
                )
                return RouteDecision(
                    action=RouteAction.COMMAND,
                    command=command,
                    actor=actor,
                    parsed_command=parsed_command,
                )

    should_respond = decide_reply(
        message.channel_type,
        message.content_kind,
        bot_id,
        bot_name,
        message.mentions,
        auto_reply_allowed,
    )
    keep = keep_in_context(
        should_respond,
        message.content_kind,
        message.has_text,
    )
    if not keep:
        if message.content_kind in _MEDIA_KINDS:
            return RouteDecision(action=RouteAction.MEDIA)
        return RouteDecision(action=RouteAction.IGNORE)
    if should_respond:
        return RouteDecision(
            action=RouteAction.REPLY,
            should_respond=True,
            keep_in_context=True,
        )
    return RouteDecision(
        action=RouteAction.CONTEXT_ONLY,
        should_respond=False,
        keep_in_context=True,
    )
