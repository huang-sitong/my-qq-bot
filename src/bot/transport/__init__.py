"""兼容层：平台接入已迁移到 ``protocol`` 上下文。"""
from protocol.http.client import SatoriApiClient
from protocol.websocket.client import SatoriClient

__all__ = ["SatoriApiClient", "SatoriClient"]
