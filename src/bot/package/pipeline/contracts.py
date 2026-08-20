"""事件流水线端口定义 — 已收敛至 domain.ports，此为兼容垫片。"""

import warnings

warnings.warn(
    "bot.package.pipeline.contracts is deprecated, use bot.package.domain.ports",
    DeprecationWarning,
    stacklevel=2,
)

from bot.package.domain.ports import ContextCompactorPort, MessageRouter, MessageSink

__all__ = ["ContextCompactorPort", "MessageRouter", "MessageSink"]
