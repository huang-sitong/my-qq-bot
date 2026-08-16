"""兼容层：消息内容领域对象已迁移到 ``conversation.content``。"""
from conversation.content import Attachment, MessageKind, ParsedContent

__all__ = ["Attachment", "MessageKind", "ParsedContent"]
