"""RagService — 组合 EmbeddingService 与 RagVectorStore。

对外提供两个异步接口：
- index_turn: 将一轮对话（用户消息 + Bot 回复）嵌入并入库；bot_reply 为空时
  只入库用户消息 1 条（群聊非@文本的被动索引）
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
        bot_id: str,
        bot_name: str,
        user_message: str,
        bot_reply: str,
    ) -> None:
        """嵌入并存储一轮对话（用户消息 + Bot 回复）。失败不抛出，仅降级。

        ``bot_reply`` 可为空（非回复轮）：此时只入库用户消息 1 条，
        避免写入空的 assistant 记录。每条记录显式建模 sender/receiver：
        - 用户消息：sender=用户(id/昵称)，receiver=bot（回复轮）或空（群广播）
        - bot 回复：sender=bot，receiver=用户
        """
        if not self.enabled:
            return
        try:
            pairs = [(user_message, "user"), (bot_reply, "assistant")]
            kept = [(c, r) for c, r in pairs if c and c.strip()]
            if not kept:
                return
            replied = bool(bot_reply.strip())
            now = int(time.time())
            vectors = await self._embedder.embed_documents([c for c, _ in kept])
            records = []
            for (content, role), vec in zip(kept, vectors):
                is_user = role == "user"
                records.append(
                    {
                        "thread_id": thread_id,
                        "sender_id": user_id if is_user else bot_id,
                        "sender_name": user_name if is_user else (bot_name or "bot"),
                        "receiver_id": (bot_id if replied else "") if is_user else user_id,
                        "receiver_name": (bot_name if replied else "") if is_user else user_name,
                        "content": content,
                        "timestamp": now,
                        "embedding": vec,
                    }
                )
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

    async def search_by_user(
        self,
        thread_id: str,
        person: str = "",
        content_keyword: str = "",
        hours: int = 0,
        limit: int = 10,
    ) -> list[dict]:
        """按发送者/接收者昵称 + 内容关键词 + 时间窗口检索（纯 SQL，无 embedding）。

        ``person`` 匹配发言者或接收者——回答"张三近期说了什么"、"bot 回复过张三什么"；
        ``content_keyword`` 匹配内容子串——回答"谁说过 xx"。失败返回空列表。
        """
        if not self.enabled:
            return []
        try:
            since_ts = int(time.time()) - hours * 3600 if hours > 0 else 0
            return await asyncio.to_thread(
                self._store.query_meta,
                thread_id, person, content_keyword, since_ts, limit,
            )
        except Exception:
            logger.exception("RAG search_by_user failed for thread %s", thread_id)
            return []

    def close(self) -> None:
        self._embedder.close()
        self._store.close()
