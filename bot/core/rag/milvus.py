"""MilvusStore — milvus-lite 向量存储与混合检索（raw pymilvus 直连）。

dense（语义）+ sparse（BM25）双字段集合，thread_id 为 partition key：
- add_texts: 文本 → EmbeddingService.embed_documents（dense）+ client.insert
  （text 字段经 BM25 内置函数自动生成 sparse）。
- search_dense / search_sparse: 字段级检索，expr + thread_id 组装过滤表达式。
- prune: 每线程超限按 timestamp DESC 淘汰最旧。
全部操作由调用方（RagService）try/except 包裹降级，失败不崩图。
"""

import asyncio
import logging

from pymilvus import DataType, Function, FunctionType, MilvusClient

from bot.core.rag.embedder import EmbeddingService

logger = logging.getLogger(__name__)

# pymilvus 单次 query 返回上限（硬限制）
_QUERY_LIMIT = 16384

# search 返回的元数据字段（动态字段 + thread_id 实字段）
_OUTPUT_FIELDS = [
    "thread_id", "sender_id", "sender_name",
    "receiver_id", "receiver_name", "content", "timestamp",
]


def _esc(value: str) -> str:
    """转义 Milvus 表达式中的字符串值（反斜杠 + 单引号）。"""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _build_filter(expr: str, thread_id: str | None) -> str:
    """把语义过滤表达式与线程隔离合并成 Milvus filter 字符串。

    expr 非空且 thread_id 给定 → 两者 ``&&``（partition key 自动路由优化）；
    仅 thread_id → 仅线程过滤；仅 expr（或两者都空）→ 原样（跨群检索）。
    """
    if expr and thread_id:
        return f"{expr} && thread_id == '{_esc(thread_id)}'"
    if thread_id:
        return f"thread_id == '{_esc(thread_id)}'"
    return expr


def _dense_hit(hit: dict) -> dict:
    """dense 命中：score = 余弦相似度（milvus-lite COSINE 直接返回相似度，越大越相关）。"""
    entity = hit.get("entity") or {}
    return {**entity, "id": hit["pk"], "score": hit["distance"]}


def _sparse_hit(hit: dict) -> dict:
    """sparse 命中：score = BM25 分值（越大越相关，无阈值）。"""
    entity = hit.get("entity") or {}
    return {**entity, "id": hit["pk"], "score": hit["distance"]}


class MilvusStore:
    """milvus-lite 存储：建集合、插入、字段级混合检索、淘汰。"""

    def __init__(
        self,
        config,
        uri: str = "./milvus.db",
        collection: str = "chat",
        embedder=None,
    ) -> None:
        self._config = config
        self._collection = collection
        self._embedder = embedder or EmbeddingService(config)
        self._client = MilvusClient(uri=uri)
        self._ensure_collection()
        logger.info("MilvusStore ready (uri=%s, collection=%s)", uri, collection)

    # ------------------------------------------------------------------
    # 集合创建
    # ------------------------------------------------------------------

    def _ensure_collection(self) -> None:
        """集合不存在才建：双向量字段 + BM25 函数 + thread_id partition key。"""
        if self._client.has_collection(self._collection):
            return
        schema = self._client.create_schema(auto_id=True, enable_dynamic_field=True)
        schema.add_field("pk", DataType.INT64, is_primary=True)
        schema.add_field("vector", DataType.FLOAT_VECTOR, dim=self._config.embed_dimensions)
        schema.add_field("sparse", DataType.SPARSE_FLOAT_VECTOR)  # 踩坑1：BM25 输出字段先声明
        schema.add_field(  # 踩坑2：analyzer 在 text 字段上，不在 Function
            "text", DataType.VARCHAR, max_length=65535,
            enable_analyzer=True, analyzer_params={"tokenizer": "jieba"},
        )
        schema.add_field(
            "thread_id", DataType.VARCHAR, max_length=128, is_partition_key=True,
        )
        schema.add_function(
            Function(
                name="bm25_fn", function_type=FunctionType.BM25,
                input_field_names=["text"], output_field_names=["sparse"],
            )
        )
        index_params = self._client.prepare_index_params()
        index_params.add_index(
            "vector", index_type="HNSW", metric_type="COSINE",
            params={"M": 8, "efConstruction": 64},
        )
        index_params.add_index(
            "sparse", index_type="SPARSE_INVERTED_INDEX", metric_type="BM25",
        )
        self._client.create_collection(self._collection, schema=schema, index_params=index_params)
        logger.info("Created milvus collection '%s'", self._collection)

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------

    async def add_texts(self, texts: list[str], metadatas: list[dict]) -> None:
        """嵌入并插入一批记录；插入后按线程淘汰超限。"""
        if not texts:
            return
        vecs = await self._embedder.embed_documents(texts)
        rows = [
            {**meta, "vector": vec, "text": text}
            for text, meta, vec in zip(texts, metadatas, vecs)
        ]
        await asyncio.to_thread(self._client.insert, self._collection, rows)
        for tid in {m["thread_id"] for m in metadatas}:
            self._prune_thread(tid, self._config.rag_retention_per_thread)

    def _prune_thread(self, thread_id: str, keep: int) -> None:
        """删除每线程超出 keep 的最旧记录（timestamp DESC）。

        milvus-lite 的 client.query 不支持 order_by（参数被静默忽略），
        故在 Python 侧按 ISO timestamp 降序排序后切片删除最旧记录。
        query 返回 HybridExtraList，字符串/动态字段懒加载——须先 list() 物化，
        否则 list.sort()（C 层）拿不到 timestamp 等字段（KeyError）。
        """
        if keep <= 0:
            return
        rows = self._client.query(
            self._collection,
            filter=f"thread_id == '{_esc(thread_id)}'",
            output_fields=["pk", "timestamp"],
            limit=_QUERY_LIMIT,
        )
        if len(rows) <= keep:
            return
        # 物化懒加载字段（timestamp 为字符串，经 __iter__ 填充到行 dict）
        rows = list(rows)
        # ISO 时间戳定宽零填充 → 字典序 == 时间序，Python 侧降序排序
        rows.sort(key=lambda r: r["timestamp"], reverse=True)
        ids = [r["pk"] for r in rows[keep:]]
        self._client.delete(self._collection, filter=f"pk in [{', '.join(map(str, ids))}]")

    def prune(self, thread_id: str, keep: int) -> None:
        """公开淘汰接口（add_texts 内部已自动淘汰；此接口供显式调用/测试）。"""
        self._prune_thread(thread_id, keep)

    # ------------------------------------------------------------------
    # 检索（字段级，D5：不用 client.query 作检索）
    # ------------------------------------------------------------------

    async def search_dense(
        self, query: str, expr: str, thread_id: str | None, k: int,
    ) -> list[dict]:
        """dense 语义检索：query 嵌入后按 vector 字段 ANN。"""
        vec = await self._embedder.embed_query(query)
        raw = await asyncio.to_thread(
            self._client.search,
            self._collection, data=[vec], anns_field="vector",
            filter=_build_filter(expr, thread_id), limit=k,
            search_params={"metric_type": "COSINE"},
            output_fields=_OUTPUT_FIELDS,
        )
        return [_dense_hit(h) for h in raw[0]]

    async def search_sparse(
        self, query: str, expr: str, thread_id: str | None, k: int,
    ) -> list[dict]:
        """sparse 词法检索：query 文本直接进 BM25 函数（jieba 分词）。"""
        raw = await asyncio.to_thread(
            self._client.search,
            self._collection, data=[query], anns_field="sparse",
            filter=_build_filter(expr, thread_id), limit=k,
            search_params={"metric_type": "BM25"},
            output_fields=_OUTPUT_FIELDS,
        )
        return [_sparse_hit(h) for h in raw[0]]

    def close(self) -> None:
        self._client.close()
        self._embedder.close()
