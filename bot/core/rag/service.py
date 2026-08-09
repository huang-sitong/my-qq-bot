"""RagService — 组合 EmbeddingService 与 MilvusStore（dense + sparse 混合检索）。

对外提供异步接口：
- index_turn:     一轮对话（用户消息 + Bot 回复）嵌入并入库
- search:         语义检索 → hybrid_search（dense+sparse，RRF 融合）
- search_by_user: 属性检索（person/content_keyword/时间窗）→ hybrid_search
- hybrid_search:  dense + sparse 候选，RRF k=60 融合，当前群优先跨群补齐
"""

import logging
from datetime import datetime, timedelta

from bot.core.rag.milvus import MilvusStore, _esc
from bot.core.rag.rrf import rrf_merge
from common import BotConfig

logger = logging.getLogger(__name__)

# 时间戳存储格式（TEXT，定宽零填充）：字典序 == 时间序，表达式直接 >= / <= 比较。
TS_FMT = "%Y-%m-%d %H:%M:%S"
# 每信号检索候选数（对齐旧 candidate_k=50）
CANDIDATE_K = 50


def normalize_time(text: str) -> str:
    """把 ISO 风格时间（YYYY-MM-DD / YYYY-MM-DD HH:MM:SS / T 分隔）规范成 TS_FMT。

    非法输入抛 ValueError（调用方应转成工具错误提示）。
    """
    return datetime.fromisoformat(text.strip()).strftime(TS_FMT)


def _build_expr(person: str, start_time: str, end_time: str) -> str:
    """组装 milvus 过滤表达式：时间窗 + 人名前缀（空段省略，以 ' && ' 连接）。"""
    conds = []
    if start_time:
        conds.append(f"timestamp >= '{start_time}'")
    if end_time:
        conds.append(f"timestamp <= '{end_time}'")
    if person:
        p = _esc(person)
        conds.append(f"(sender_name like '{p}%' || receiver_name like '{p}%')")
    return " && ".join(conds)


class RagService:
    """群聊历史 RAG：索引一轮对话，混合检索相关历史。"""

    def __init__(self, config: BotConfig, store: MilvusStore | None = None) -> None:
        self.config = config
        self._store = store or MilvusStore(config)

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

        ``bot_reply`` 可为空（非回复轮）：此时只入库用户消息 1 条。
        每条记录显式建模 sender/receiver：
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
            texts = [c for c, _ in kept]
            metadatas = []
            for content, role in kept:
                is_user = role == "user"
                metadatas.append(
                    {
                        "thread_id": thread_id,
                        "sender_id": user_id if is_user else bot_id,
                        "sender_name": user_name if is_user else (bot_name or "bot"),
                        "receiver_id": (bot_id if replied else "") if is_user else user_id,
                        "receiver_name": (bot_name if replied else "") if is_user else user_name,
                        "timestamp": now,
                        # content 是动态字段，供 search 输出（text 仅作 BM25 输入，
                        # _OUTPUT_FIELDS 与工具渲染都依赖 content，缺则 KeyError 降级）
                        "content": content,
                    }
                )
            await self._store.add_texts(texts, metadatas)
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
        hours: int = 0,
        start_time: str = "",
        end_time: str = "",
    ) -> list[dict]:
        """语义检索（query 兼作 dense+sparse 信号）。失败返回空列表。"""
        return await self.hybrid_search(
            query=query, thread_id=thread_id,
            hours=hours, start_time=start_time, end_time=end_time,
            top_k=top_k,
        )

    async def search_by_user(
        self,
        thread_id: str | None = None,
        person: str = "",
        content_keyword: str = "",
        hours: int = 0,
        start_time: str = "",
        end_time: str = "",
        limit: int = 10,
    ) -> list[dict]:
        """按发送者/接收者昵称 + 内容关键词 + 时间窗口检索。

        ``thread_id`` 为 None 时检索**全部群**（属性检索跨群），否则限定该群。
        ``person`` 进 expr 前缀匹配发言者/接收者；``content_keyword`` 作 sparse
        信号（查"谁说过 xx"）。时间窗口二选一：``hours`` 相对窗口 /
        ``start_time``/``end_time`` 绝对边界。失败返回空列表。
        """
        return await self.hybrid_search(
            query="", thread_id=thread_id, person=person,
            content_keyword=content_keyword,
            hours=hours, start_time=start_time, end_time=end_time,
            top_k=limit,
        )

    async def hybrid_search(
        self,
        query: str,
        thread_id: str,
        person: str = "",
        content_keyword: str = "",
        hours: int = 0,
        start_time: str = "",
        end_time: str = "",
        top_k: int | None = None,
    ) -> list[dict]:
        """dense + sparse 双信号候选，RRF 融合；当前群优先，不足跨群补齐。

        - dense：query 非空才跑，候选按 ``rag_score_threshold`` 过滤；
        - sparse：``content_keyword or query`` 非空才跑，无阈值；
        - 本群融合结果不足 top_k 且 thread_id 非空 → 跨群补齐（expr 仍生效）；
        - 最终 RRF 融合后截断到 top_k。
        person-only（无 content/query）→ 返回空。
        """
        if not self.enabled:
            return []
        try:
            if hours > 0 and not start_time:
                start_time = (datetime.now() - timedelta(hours=hours)).strftime(TS_FMT)
            expr = _build_expr(person, start_time, end_time)
            limit = top_k or self.config.rag_top_k

            dense: list[dict] = []
            if query.strip():
                dense = await self._store.search_dense(query, expr, thread_id, CANDIDATE_K)
                dense = [h for h in dense if h.get("score", 0.0) >= self.config.rag_score_threshold]
            sparse_kw = content_keyword.strip() or query.strip()
            sparse: list[dict] = []
            if sparse_kw:
                sparse = await self._store.search_sparse(sparse_kw, expr, thread_id, CANDIDATE_K)

            # 当前群候选不足 → 跨群补齐（thread_id=None，expr 仍生效）
            # query 非空才补齐：属性路径（query=""，如 search_by_user 限定单群）不跨群
            if query.strip() and thread_id and len({h["id"] for h in dense + sparse}) < limit:
                if query.strip():
                    dense_x = await self._store.search_dense(query, expr, None, CANDIDATE_K)
                    dense += [h for h in dense_x if h.get("score", 0.0) >= self.config.rag_score_threshold]
                if sparse_kw:
                    sparse += await self._store.search_sparse(sparse_kw, expr, None, CANDIDATE_K)

            return rrf_merge([dense, sparse])[:limit]
        except Exception:
            logger.exception("RAG hybrid_search failed for thread %s", thread_id)
            return []

    def close(self) -> None:
        self._store.close()
