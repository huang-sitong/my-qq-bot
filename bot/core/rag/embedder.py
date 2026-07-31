"""Embedding 服务：封装 OllamaEmbeddings（原生 API），qwen3-embedding Instruct 格式。

qwen3-embedding 是对话模板模型，检索时需加 Instruct 前缀才能达到最佳区分度
（验证见 test/test_ollama_embedding.py 测试项 5）。
"""

import asyncio
import logging

from langchain_ollama import OllamaEmbeddings

from common import BotConfig

logger = logging.getLogger(__name__)

# 检索任务描述，Query 与 Document 共用，保持向量空间一致
RETRIEVAL_TASK = "检索群聊历史中与问题最相关的消息"


class EmbeddingService:
    """为查询和文档生成向量。同步调用包装为线程以不阻塞事件循环。"""

    def __init__(self, config: BotConfig) -> None:
        self._config = config
        self._embeddings = OllamaEmbeddings(
            model=config.embed_model,
            base_url=config.ollama_base_url,
            dimensions=config.embed_dimensions,
        )
        logger.info(
            "EmbeddingService ready: model=%s dimensions=%s base_url=%s",
            config.embed_model, config.embed_dimensions, config.ollama_base_url,
        )

    def _query_text(self, query: str) -> str:
        return f"Instruct: {RETRIEVAL_TASK}\nQuery: {query}"

    def _document_text(self, content: str) -> str:
        return f"Instruct: {RETRIEVAL_TASK}\nDocument: {content}"

    async def embed_query(self, query: str) -> list[float]:
        return await asyncio.to_thread(
            self._embeddings.embed_query, self._query_text(query)
        )

    async def embed_documents(self, contents: list[str]) -> list[list[float]]:
        if not contents:
            return []
        texts = [self._document_text(c) for c in contents]
        return await asyncio.to_thread(self._embeddings.embed_documents, texts)
