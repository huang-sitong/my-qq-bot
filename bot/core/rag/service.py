"""RagService — 组合 EmbeddingService 与 RagVectorStore。

对外提供两个异步接口：
- index_turn: 将一轮对话（用户消息 + Bot 回复）嵌入并入库；bot_reply 为空时
  只入库用户消息 1 条（群聊非@文本的被动索引）
- search:    按查询词检索历史，返回格式化上下文
"""

import asyncio
import logging
from datetime import datetime, timedelta

from common import BotConfig
from bot.core.rag.embedder import EmbeddingService
from bot.core.rag.store import RagVectorStore

logger = logging.getLogger(__name__)

# 时间戳存储格式（TEXT，定宽零填充）：字典序 == 时间序，SQL 直接 >= / <= 比较。
# 展示层（search_chat_history._format_time）截到分钟，库里保留秒。
TS_FMT = "%Y-%m-%d %H:%M:%S"


def normalize_time(text: str) -> str:
    """把 ISO 风格时间（YYYY-MM-DD / YYYY-MM-DD HH:MM:SS / T 分隔）规范成 TS_FMT。

    非法输入抛 ValueError（调用方应转成工具错误提示）。
    """
    return datetime.fromisoformat(text.strip()).strftime(TS_FMT)


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
            now = datetime.now().strftime(TS_FMT)
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
        hours: int = 0,
        start_time: str = "",
        end_time: str = "",
    ) -> list[dict]:
        """检索相关历史。失败时返回空列表（不阻塞对话）。

        ``hours``/``start_time``/``end_time`` 与 search_by_user 同语义——
        时间窗口在语义检索同样生效（检索后剪枝）。
        """
        if not self.enabled:
            return []
        try:
            if hours > 0 and not start_time:
                start_time = (datetime.now() - timedelta(hours=hours)).strftime(TS_FMT)
            vec = await self._embedder.embed_query(query)
            return await asyncio.to_thread(
                self._store.search,
                vec,
                thread_id,
                top_k or self.config.rag_top_k,
                score_threshold or self.config.rag_score_threshold,
                start_time,
                end_time,
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
        start_time: str = "",
        end_time: str = "",
        limit: int = 10,
    ) -> list[dict]:
        """按发送者/接收者昵称 + 内容关键词 + 时间窗口检索（纯 SQL，无 embedding）。

        ``person`` 匹配发言者或接收者——回答"张三近期说了什么"、"bot 回复过张三什么"；
        ``content_keyword`` 匹配内容子串——回答"谁说过 xx"。时间窗口二选一：
        ``hours`` 相对最近 N 小时；``start_time``/``end_time`` 绝对边界
        （ISO 风格字符串，由调用方 normalize_time 规范化）。失败返回空列表。
        """
        if not self.enabled:
            return []
        try:
            if hours > 0 and not start_time:
                start_time = (datetime.now() - timedelta(hours=hours)).strftime(TS_FMT)
            return await asyncio.to_thread(
                self._store.query_meta,
                thread_id=thread_id,
                person=person,
                content_keyword=content_keyword,
                since_iso=start_time,
                until_iso=end_time,
                limit=limit,
            )
        except Exception:
            logger.exception("RAG search_by_user failed for thread %s", thread_id)
            return []

    def close(self) -> None:
        self._embedder.close()
        self._store.close()
