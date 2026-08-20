"""Satori 协议常量 — 避免上层反向依赖 platform。"""

# Satori ChannelType.DIRECT 的整数值；bot.utils.routing 不应反向依赖 platform 包，
# 因此在此保存协议共享常量（与原 domain.constants.DIRECT_CHANNEL_TYPE 同值）。
DIRECT_CHANNEL_TYPE = 1

__all__ = ["DIRECT_CHANNEL_TYPE"]
