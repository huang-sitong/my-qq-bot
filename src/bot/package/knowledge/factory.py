"""知识上下文工厂：统一 RAG / 文档存储的创建与降级策略。

供 ``core.boot`` 调用，内部处理 ``rag_enabled`` 开关与异常降级，避免装配根散落 try/except。
"""

from __future__ import annotations

import logging

from bot.package.config import BotConfig

from .document_store import DocumentStore
from .service import RagService

logger = logging.getLogger(__name__)


def create_rag_service(config: BotConfig) -> RagService | None:
    """创建 RagService；禁用或失败时返回 None（调用方降级）。"""
    if not config.rag_enabled:
        return None
    try:
        return RagService(config)
    except Exception:
        logger.exception("RAG init failed; falling back to rag disabled")
        return None


def create_document_store(config: BotConfig, embedder=None) -> DocumentStore | None:
    """创建 DocumentStore；复用已创建的 embedder 以避免重复打开缓存。"""
    if not config.rag_enabled:
        return None
    try:
        return DocumentStore(config, collection=config.document_collection, embedder=embedder)
    except Exception:
        logger.exception("DocumentStore init failed; document search disabled")
        return None
