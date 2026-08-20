"""DocumentStore — 文档知识库存储适配器。

实现 ``domain.repositories.DocumentRepository``；文档与聊天记录隔离，
使用独立的 milvus-lite collection（默认 ``documents``）。
- schema 固定字段比 ``chat`` 多出文档元信息：doc_id / file_hash / file_name / file_type / page / chunk_index / source_path；
- add_texts 不参与聊天记录按线程淘汰；
- 提供按 file_hash 去重、按 doc_id 删除等文档生命周期操作。
"""

from __future__ import annotations

import asyncio
import logging

from pymilvus import DataType, Function, FunctionType

from .milvus import MilvusStore, _esc

logger = logging.getLogger(__name__)

# 文档检索默认返回的元数据字段
DOC_OUTPUT_FIELDS = [
    "thread_id",
    "doc_id",
    "file_hash",
    "file_name",
    "file_type",
    "page",
    "chunk_index",
    "source_path",
    "imported_at",
    "content",
]


class DocumentStore(MilvusStore):
    """milvus-lite 文档集合：建集合、写入、去重、删除、检索。"""

    def __init__(
        self,
        config,
        uri: str | None = None,
        collection: str = "documents",
        embedder=None,
    ) -> None:
        super().__init__(
            config,
            uri=uri,
            collection=collection,
            embedder=embedder,
            prune_on_add=False,
        )

    # ------------------------------------------------------------------
    # 集合创建
    # ------------------------------------------------------------------

    def _create_collection(self) -> None:
        """文档集合 schema：双向量 + BM25 + 文档元信息字段。"""
        schema = self._client.create_schema(auto_id=True, enable_dynamic_field=True)
        schema.add_field("pk", DataType.INT64, is_primary=True)
        schema.add_field("vector", DataType.FLOAT_VECTOR, dim=self._config.embed_dimensions)
        schema.add_field("sparse", DataType.SPARSE_FLOAT_VECTOR)
        schema.add_field(
            "text", DataType.VARCHAR, max_length=65535,
            enable_analyzer=True, analyzer_params={"tokenizer": "jieba"},
        )
        schema.add_field("doc_id", DataType.VARCHAR, max_length=128)
        schema.add_field("file_hash", DataType.VARCHAR, max_length=64)
        schema.add_field("file_name", DataType.VARCHAR, max_length=512)
        schema.add_field("file_type", DataType.VARCHAR, max_length=16)
        schema.add_field("page", DataType.INT64)
        schema.add_field("chunk_index", DataType.INT64)
        schema.add_field("source_path", DataType.VARCHAR, max_length=1024)
        schema.add_field("imported_at", DataType.VARCHAR, max_length=32)
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
        logger.info("Created milvus document collection '%s'", self._collection)

    # ------------------------------------------------------------------
    # 文档生命周期
    # ------------------------------------------------------------------

    async def has_doc(self, file_hash: str) -> bool:
        """按内容哈希判断文件是否已导入。"""
        async with self._client_lock:
            rows = await asyncio.to_thread(
                self._client.query,
                self._collection,
                filter=f"file_hash == '{_esc(file_hash)}'",
                output_fields=["pk"],
                limit=1,
            )
        return bool(rows)

    async def delete_doc(self, doc_id: str) -> int:
        """按 doc_id 删除该文件的所有 chunks，返回删除条数。"""
        async with self._client_lock:
            rows = await asyncio.to_thread(
                self._client.query,
                self._collection,
                filter=f"doc_id == '{_esc(doc_id)}'",
                output_fields=["pk"],
                limit=16384,
            )
            if not rows:
                return 0
            pks = [r["pk"] for r in rows]
            await asyncio.to_thread(
                self._client.delete,
                self._collection,
                filter=f"pk in [{', '.join(map(str, pks))}]",
            )
        return len(pks)

    # ------------------------------------------------------------------
    # 检索（默认返回文档字段）
    # ------------------------------------------------------------------

    async def search_dense(
        self,
        query: str,
        expr: str,
        thread_id: str | None,
        k: int,
        output_fields: list[str] | None = None,
    ) -> list[dict]:
        return await super().search_dense(
            query, expr, thread_id, k,
            output_fields=output_fields or DOC_OUTPUT_FIELDS,
        )

    async def search_sparse(
        self,
        query: str,
        expr: str,
        thread_id: str | None,
        k: int,
        output_fields: list[str] | None = None,
    ) -> list[dict]:
        return await super().search_sparse(
            query, expr, thread_id, k,
            output_fields=output_fields or DOC_OUTPUT_FIELDS,
        )
