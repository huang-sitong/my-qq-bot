"""平台适配层包。

当前仅有 ``satori`` 实现；新平台可在此增加子包并注册到
:data:`PLATFORM_ADAPTERS`。
"""

from .base import EventSource, PlatformAdapter

PLATFORM_ADAPTERS: dict[str, str] = {
    "satori": "bot.package.platform.satori.adapter:SatoriAdapter",
}

_SATORI_EXPORTS = {"SatoriAdapter", "SatoriApiClient", "SatoriClient", "SatoriMessageIngress"}


def __getattr__(name: str):
    if name in _SATORI_EXPORTS:
        import importlib

        module_name = ".satori.adapter" if name == "SatoriAdapter" else ".satori"
        module = importlib.import_module(module_name, __package__)
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "PLATFORM_ADAPTERS",
    "EventSource",
    "PlatformAdapter",
    "SatoriAdapter",
    "SatoriApiClient",
    "SatoriClient",
    "SatoriMessageIngress",
]
