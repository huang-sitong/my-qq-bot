"""平台适配层包。

当前仅有 ``satori`` 实现；新平台可在此增加子包并注册到
:data:`PLATFORM_ADAPTERS`。
显式导出，无懒加载魔法。
"""

from .base import EventSource, PlatformAdapter
from .satori.adapter import SatoriAdapter
from .satori.http import SatoriApiClient
from .satori.ingress import SatoriMessageIngress
from .satori.websocket import SatoriClient

PLATFORM_ADAPTERS: dict[str, str] = {
    "satori": "bot.package.platform.satori.adapter:SatoriAdapter",
}

__all__ = [
    "PLATFORM_ADAPTERS",
    "EventSource",
    "PlatformAdapter",
    "SatoriAdapter",
    "SatoriApiClient",
    "SatoriClient",
    "SatoriMessageIngress",
]
