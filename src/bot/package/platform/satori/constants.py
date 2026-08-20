"""Satori 协议常量 — 单一来源在会话领域策略。

``DIRECT_CHANNEL_TYPE`` 的语义权威属于会话领域
（``bot.package.conversation.policy``），此处仅做平台侧兼容反引；
Satori ``ChannelType.DIRECT`` 与其同值。
"""

from bot.package.conversation.policy import DIRECT_CHANNEL_TYPE

__all__ = ["DIRECT_CHANNEL_TYPE"]
