"""RagService — 组合 EmbeddingService 与 RagVectorStore。

对外提供两个异步接口：
- index_turn: 将一轮对话（用户消息 + Bot 回复）嵌入并入库
- search:    按查询词检索历史，返回格式化上下文
"""

import asyncio
import logging
import time

from common import BotConfig
from bot.core.rag.embedder import EmbeddingService
from bot.core.rag.store import RagVectorStore

logger = logging.getLogger(__name__)


class RagService:
    """群聊历史 RAG：索引一轮对话，按需检索相关历史。"""

    def __init__(
        self,
        config: BotConfig,
        embedder: EmbeddingService | None = None,
        store: RagVectorStore | None = None,
    ) -> None:
        self.config = config
        self._embedder = embedder or EmbeddingService(config)
        self._store = store or RagVectorStore(
            db_dir=config.db_dir,
            dimensions=config.embed_dimensions,
            retention_per_thread=config.rag_retention_per_thread,
        )

    @property
    def enabled(self) -> bool:
        return self.config.rag_enabled

    # ------------------------------------------------------------------
    # 索引
    # ------------------------------------------------------------------

    async def index_turn(
        self,
        thread_id: str,
        user_id: str,
        user_name: str,
        user_message: str,
        bot_reply: str,
    ) -> None:
        """嵌入并存储一轮对话（用户消息 + Bot 回复）。失败不抛出，仅降级。"""
        if not self.enabled:
            return
        try:
            contents = [user_message, bot_reply]
            vectors = await self._embedder.embed_documents(contents)
            now = int(time.time())
            records = [
                {
                    "thread_id": thread_id,
                    "user_id": user_id,
                    "user_name": user_name or "",
                    "content": user_message,
                    "role": "user",
                    "timestamp": now,
                    "embedding": vectors[0],
                },
                {
                    "thread_id": thread_id,
                    "user_id": user_id,
                    "user_name": user_name or "",
                    "content": bot_reply,
                    "role": "assistant",
                    "timestamp": now,
                    "embedding": vectors[1],
                },
            ]
            await asyncio.to_thread(self._store.add, records)
        except Exception:
            logger.exception("RAG index_turn failed for thread %s", thread_id)

    # ------------------------------------------------------------------
    # 检索
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        thread_id: str,
        top_k: int | None = None,
        score_threshold: float | None = None,
    ) -> list[dict]:
        """检索相关历史。失败时返回空列表（不阻塞对话）。"""
        if not self.enabled:
            return []
        try:
            vec = await self._embedder.embed_query(query)
            return await asyncio.to_thread(
                self._store.search,
                vec,
                thread_id,
                top_k or self.config.rag_top_k,
                score_threshold or self.config.rag_score_threshold,
            )
        except Exception:
            logger.exception("RAG search failed for thread %s", thread_id)
            return []

    def close(self) -> None:
        self._embedder.close()
        self._store.close()
