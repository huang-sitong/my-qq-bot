"""跨模块共享常量 — 已拆分，此为兼容垫片。"""

import warnings

warnings.warn(
    "bot.package.domain.constants is deprecated, use bot.package.orchestration.constants / bot.package.platform.satori.constants",
    DeprecationWarning,
    stacklevel=2,
)

EXTERNAL_UPDATE_NODE = "describe_image"
DIRECT_CHANNEL_TYPE = 1

__all__ = ["DIRECT_CHANNEL_TYPE", "EXTERNAL_UPDATE_NODE"]
