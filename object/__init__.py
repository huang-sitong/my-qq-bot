"""Top-level re-exports for the ``object`` package.

Uses the same lazy-loading ``__getattr__`` pattern as the sub-packages so
that imports only load the modules they actually use.
"""

# 分组注释是刻意结构（与下方 _module_map 平行同步），按 RUF022 全量字母序会打散分组
__all__ = [  # noqa: RUF022
    # bot
    "BotState",
    "Attachment",
    "MessageKind",
    "ParsedContent",
    # satori — enums
    "ChannelType",
    "Direction",
    "LoginStatus",
    "Order",
    # satori — models
    "Argv",
    "BidiList",
    "Button",
    "Channel",
    "Emoji",
    "Friend",
    "Guild",
    "GuildMember",
    "GuildRole",
    "Login",
    "Message",
    "PageList",
    "User",
    # satori — events
    "EventBody",
    "LoginList",
    "Signal",
    # satori — api (commonly used endpoints + params)
    "Endpoint",
    "MESSAGE_CREATE",
    "MESSAGE_GET",
    "MESSAGE_LIST",
    "MessageCreateParams",
    "MessageGetParams",
    "MessageListParams",
]

# fmt: off
# ---------------------------------------------------------------------------
# Auto-generated: map every public name to the sub-package that owns it.
# ``__getattr__`` delegates to the sub-package, which in turn lazy-loads
# from the correct sub-module.
# ---------------------------------------------------------------------------
_module_map: dict[str, str] = {}
# 必须与上方 __all__ 的 # bot 组保持同步：新增 bot 导出名若漏加此集合，会被误映射到 "satori"
_BOT_NAMES = {"BotState", "Attachment", "MessageKind", "ParsedContent"}
for _name in __all__:
    _module_map[_name] = "bot" if _name in _BOT_NAMES else "satori"


def __getattr__(name: str):
    """Lazy-load *name* from the appropriate sub-package."""
    sub_pkg = _module_map.get(name)
    if sub_pkg is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module = importlib.import_module(f".{sub_pkg}", __package__)
    return getattr(module, name)


def __dir__() -> list[str]:
    return sorted(__all__)
