"""Satori 协议领域模型导出。

``domain`` 现在只保留协议相关的共享模型；业务领域模型已按限界上下文拆分到
``commands`` / ``conversation`` / ``skill`` / ``knowledge`` / ``memory`` / ``vision``。
"""

__all__ = [
    "MESSAGE_CREATE",
    "MESSAGE_GET",
    "MESSAGE_LIST",
    "Argv",
    "BidiList",
    "Button",
    "Channel",
    "ChannelType",
    "Direction",
    "Emoji",
    "Endpoint",
    "EventBody",
    "Friend",
    "Guild",
    "GuildMember",
    "GuildRole",
    "Login",
    "LoginList",
    "LoginStatus",
    "Message",
    "MessageCreateParams",
    "MessageGetParams",
    "MessageListParams",
    "Order",
    "PageList",
    "Signal",
    "User",
]

_module_map: dict[str, str] = {name: "satori" for name in __all__}


def __getattr__(name: str):
    module_name = _module_map.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module = importlib.import_module(f".{module_name}", __package__)
    return getattr(module, name)


def __dir__() -> list[str]:
    return sorted(__all__)
