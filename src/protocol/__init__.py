"""协议接入限界上下文。

负责 Satori/OneBot 协议连接、事件接收、消息/文件发送等外部协议适配。
"""

from .http.client import SatoriApiClient
from .websocket.client import SatoriClient

__all__ = ["SatoriApiClient", "SatoriClient"]
