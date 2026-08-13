__all__ = [
    "Attachment",
    "BashConfig",
    "BotState",
    "Command",
    "CommandActor",
    "CommandContext",
    "CommandResult",
    "CommandServices",
    "IncomingMessage",
    "IndexTurnTask",
    "MessageKind",
    "ParsedCommand",
    "ParsedContent",
    "RouteAction",
    "RouteDecision",
    "Skill",
]

_module_map = {
    "Attachment": "content",
    "BashConfig": "bash",
    "BotState": "state",
    "Command": "command",
    "CommandActor": "command",
    "CommandContext": "command",
    "CommandResult": "command",
    "CommandServices": "command",
    "IncomingMessage": "message",
    "IndexTurnTask": "index_task",
    "MessageKind": "content",
    "ParsedCommand": "command",
    "ParsedContent": "content",
    "RouteAction": "router",
    "RouteDecision": "router",
    "Skill": "skill",
}


def __getattr__(name):
    module_name = _module_map.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib
    module = importlib.import_module(f".{module_name}", __package__)
    return getattr(module, name)


def __dir__():
    return sorted(__all__)
