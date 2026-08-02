"""Embedding 服务：封装 OllamaEmbeddings（原生 API），qwen3-embedding Instruct 格式。

qwen3-embedding 是对话模板模型，检索时需加 Instruct 前缀才能达到最佳区分度
（验证见 test/test_ollama_embedding.py 测试项 5）。
嵌入结果按 (model, 文本) 哈希写入磁盘缓存（EmbeddingCache），重复文本直接命中，
不再重复调 Ollama。
"""

import asyncio
import hashlib
import logging
import os

from langchain_ollama import OllamaEmbeddings

from bot.core.rag.cache import EmbeddingCache
from common import BotConfig, RETRIEVAL_TASK

logger = logging.getLogger(__name__)


class EmbeddingService:
    """为查询和文档生成向量。同步调用包装为线程以不阻塞事件循环。"""

    def __init__(
        self,
        config: BotConfig,
        embedder: OllamaEmbeddings | None = None,
        cache: EmbeddingCache | None = None,
    ) -> None:
        self._config = config
        self._embeddings = embedder or OllamaEmbeddings(
            model=config.embed_model,
            base_url=config.ollama_base_url,
            dimensions=config.embed_dimensions,
        )
        # 未显式注入缓存时，按配置自动创建（embed_cache_enabled 开关）
        if cache is None and getattr(config, "embed_cache_enabled", True):
            cache = EmbeddingCache(
                db_path=os.path.join(config.db_dir, "embed_cache.sqlite"),
                max_entries=getattr(config, "embed_cache_max_entries", 20000),
            )
        self._cache = cache
        logger.info(
            "EmbeddingService ready: model=%s dimensions=%s base_url=%s cache=%s",
            config.embed_model, config.embed_dimensions, config.ollama_base_url,
            "on" if cache is not None else "off",
        )

    def _query_text(self, query: str) -> str:
        return f"Instruct: {RETRIEVAL_TASK}\nQuery: {query}"

    def _document_text(self, content: str) -> str:
        return f"Instruct: {RETRIEVAL_TASK}\nDocument: {content}"

    def _cache_key(self, text: str) -> str:
        """缓存 key：model 变化时自动失效（换模型 → 新 key 空间）。"""
        return hashlib.sha256(f"{self._config.embed_model}\x00{text}".encode("utf-8")).hexdigest()

    async def embed_query(self, query: str) -> list[float]:
        text = self._query_text(query)
        if self._cache is not None:
            key = self._cache_key(text)
            cached = await asyncio.to_thread(self._cache.get, key)
            if cached is not None:
                return cached
            vec = await asyncio.to_thread(self._embeddings.embed_query, text)
            await asyncio.to_thread(self._cache.set, key, self._config.embed_model, text, vec)
            return vec
        return await asyncio.to_thread(self._embeddings.embed_query, text)

    async def embed_documents(self, contents: list[str]) -> list[list[float]]:
        if not contents:
            return []
        texts = [self._document_text(c) for c in contents]
        if self._cache is None:
            return await asyncio.to_thread(self._embeddings.embed_documents, texts)
        keys = [self._cache_key(t) for t in texts]
        cached = await asyncio.to_thread(self._cache.mget, keys)
        missing = [(i, t) for i, t in enumerate(texts) if cached[i] is None]
        if missing:
            idxs = [i for i, _ in missing]
            vecs = await asyncio.to_thread(
                self._embeddings.embed_documents, [t for _, t in missing]
            )
            pairs = [
                (keys[i], self._config.embed_model, texts[i], v)
                for i, v in zip(idxs, vecs)
            ]
            await asyncio.to_thread(self._cache.mset, pairs)
            for i, v in zip(idxs, vecs):
                cached[i] = v
        return cached

    def close(self) -> None:
        if self._cache is not None:
            self._cache.close()
