"""兼容层：领域消息已迁移到 ``conversation.message``。"""
from conversation.message import IncomingMessage

__all__ = ["IncomingMessage"]
