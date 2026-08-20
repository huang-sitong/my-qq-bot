from bot.package.commands import CommandActor, CommandRegistry, parse_command
from bot.package.conversation.content import MessageKind
from bot.package.conversation.conversation import Conversation
from bot.package.conversation.message import IncomingMessage
from bot.package.conversation.router import RouteAction, RouteDecision

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

    conversation = Conversation.from_message(
        message,
        bot_id=bot_id,
        bot_name=bot_name,
    )
    reply = conversation.decide(
        message,
        auto_reply=auto_reply_allowed,
    )
    should_respond = reply.should_respond
    keep = reply.keep_in_context
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
