__all__ = [
    "BotState",
    "Attachment",
    "MessageKind",
    "ParsedContent",
]

_module_map = {
    "BotState": "state",
    "Attachment": "content",
    "MessageKind": "content",
    "ParsedContent": "content",
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
