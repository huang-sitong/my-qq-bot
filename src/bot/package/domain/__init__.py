"""共享领域模型导出 — 仅跨 3+ 上下文共享的 DTO 与端口。"""

from .events import DomainEvent, DomainEventBus
from .media import ImageDescription
from .ports import (
    ContextCompactorPort,
    MemoryRepository,
    MessageQueue,
    MessageRouter,
    MessageSender,
    MessageSink,
    RagIndexer,
    VisionServicePort,
)
from .repositories import ConversationRepository, DocumentRepository
from .tasks import IndexTurnTask

__all__ = [
    "ContextCompactorPort",
    "ConversationRepository",
    "DocumentRepository",
    "DomainEvent",
    "DomainEventBus",
    "ImageDescription",
    "IndexTurnTask",
    "MemoryRepository",
    "MessageQueue",
    "MessageRouter",
    "MessageSender",
    "MessageSink",
    "RagIndexer",
    "VisionServicePort",
]
