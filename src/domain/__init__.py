"""共享领域模型导出。

- Satori 协议相关模型位于 ``domain.satori``
- 跨上下文共享 DTO 位于 ``domain.tasks`` / ``domain.media`` / ``domain.bash``
- 端口抽象位于 ``domain.ports``

业务领域模型按限界上下文拆分到
``commands`` / ``conversation`` / ``skill`` / ``knowledge`` / ``memory`` / ``vision``。
"""

__all__ = [
    "MESSAGE_CREATE",
    "MESSAGE_GET",
    "MESSAGE_LIST",
    "Argv",
    "BashConfig",
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
    "ImageDescription",
    "IndexTurnTask",
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
_module_map["BashConfig"] = "bash"
_module_map["ImageDescription"] = "media"
_module_map["IndexTurnTask"] = "tasks"


def __getattr__(name: str):
    module_name = _module_map.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module = importlib.import_module(f".{module_name}", __package__)
    return getattr(module, name)


def __dir__() -> list[str]:
    return sorted(__all__)
