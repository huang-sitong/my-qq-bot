"""Embedding 服务：封装 OpenAI 兼容嵌入端点，qwen3-embedding Instruct 格式。

qwen3-embedding 是对话模板模型，检索时需加 Instruct 前缀才能达到最佳区分度
（当前无实机验证测试；缓存侧 text 列不带前缀见 tests/test_embed_cache.py）。
嵌入结果按 (model, 任务前缀, 角色, 原始内容) 哈希写入磁盘缓存（EmbeddingCache），
重复文本直接命中，不再重复调嵌入 API；缓存 text 列只存原始内容（不带 Instruct 前缀）。
"""

import asyncio
import hashlib
import logging
import os

from langchain_core.embeddings import Embeddings
from langchain_openai import OpenAIEmbeddings

from common import RETRIEVAL_TASK, BotConfig
from common.retry import retry_async

from .cache import EmbeddingCache

logger = logging.getLogger(__name__)


def _embedding_base_url(base_url: str | None) -> str | None:
    """把 OpenAI 兼容根地址归一化到 ``/v1``；已带 ``/v1`` 时原样返回。"""
    if not base_url:
        return None
    stripped = base_url.rstrip("/")
    return stripped if stripped.endswith("/v1") else f"{stripped}/v1"


class EmbeddingService:
    """为查询和文档生成向量。同步调用包装为线程以不阻塞事件循环。"""

    def __init__(
        self,
        config: BotConfig,
        embedder: Embeddings | None = None,
        cache: EmbeddingCache | None = None,
    ) -> None:
        self._config = config
        self._embeddings = embedder or OpenAIEmbeddings(
            model=config.embed_model,
            api_key=config.embed_api_key,
            base_url=_embedding_base_url(config.embed_base_url),
            dimensions=config.embed_dimensions,
            check_embedding_ctx_length=False,
        )
        # 未显式注入缓存时，按配置自动创建（embed_cache_enabled 开关）
        if cache is None and getattr(config, "embed_cache_enabled", True):
            cache = EmbeddingCache(
                db_path=os.path.join(config.db_dir, "embed_cache.sqlite"),
                max_entries=getattr(config, "embed_cache_max_entries", 20000),
            )
        self._cache = cache
        self._embed_lock = asyncio.Lock()
        logger.info(
            "EmbeddingService ready: model=%s dimensions=%s base_url=%s cache=%s",
            config.embed_model, config.embed_dimensions, config.embed_base_url,
            "on" if cache is not None else "off",
        )

    def _query_text(self, query: str) -> str:
        return f"Instruct: {RETRIEVAL_TASK}\nQuery: {query}"

    def _document_text(self, content: str) -> str:
        return f"Instruct: {RETRIEVAL_TASK}\nDocument: {content}"

    def _cache_key(self, role: str, raw: str) -> str:
        """缓存 key 覆盖影响向量的全部变体：model / 任务前缀 / 角色 / 原始内容。

        任一变化 → 新 key 空间，旧缓存自然失效（换模型、改 RETRIEVAL_TASK、
        Query/Document 角色互换都互不串用）；text 列只存原始内容，前缀不进缓存。
        """
        return hashlib.sha256(
            f"{self._config.embed_model}\x00{RETRIEVAL_TASK}\x00{role}\x00{raw}".encode()
        ).hexdigest()

    async def _embed_query_once(self, text: str) -> list[float]:
        return await asyncio.to_thread(self._embeddings.embed_query, text)

    async def _embed_documents_once(self, texts: list[str]) -> list[list[float]]:
        return await asyncio.to_thread(self._embeddings.embed_documents, texts)

    async def embed_query(self, query: str) -> list[float]:
        text = self._query_text(query)
        if self._cache is not None:
            key = self._cache_key("query", query)
            cached = await asyncio.to_thread(self._cache.get, key)
            if cached is not None:
                return cached
            async with self._embed_lock:
                vec = await retry_async(
                    self._embed_query_once, text, retries=2, logger=logger,
                )
            await asyncio.to_thread(self._cache.set, key, self._config.embed_model, query, vec)
            return vec
        async with self._embed_lock:
            return await retry_async(
                self._embed_query_once, text, retries=2, logger=logger,
            )

    async def embed_documents(self, contents: list[str]) -> list[list[float]]:
        if not contents:
            return []
        texts = [self._document_text(c) for c in contents]
        if self._cache is None:
            async with self._embed_lock:
                return await retry_async(
                    self._embed_documents_once, texts, retries=2, logger=logger,
                )
        keys = [self._cache_key("document", c) for c in contents]
        cached = await asyncio.to_thread(self._cache.mget, keys)
        missing = [(i, t) for i, t in enumerate(texts) if cached[i] is None]
        if missing:
            idxs = [i for i, _ in missing]
            missing_texts = [t for _, t in missing]
            async with self._embed_lock:
                vecs = await retry_async(
                    self._embed_documents_once,
                    missing_texts,
                    retries=2,
                    logger=logger,
                )
            pairs = [
                (keys[i], self._config.embed_model, contents[i], v)
                for i, v in zip(idxs, vecs)
            ]
            await asyncio.to_thread(self._cache.mset, pairs)
            for i, v in zip(idxs, vecs):
                cached[i] = v
        return cached

    def close(self) -> None:
        if self._cache is not None:
            self._cache.close()
