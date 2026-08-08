"""MilvusStore 集成测试（真实 milvus-lite，tmp_path 临时库）。

覆盖：add_texts 写库、search_dense / search_sparse、线程隔离（expr）、
expr 过滤（人名 / 时间窗）、prune 淘汰、dense score 语义。
测试注入 FakeEmbedder（确定性向量），不连 Ollama。
"""

import asyncio

import pytest

from bot.core.rag.milvus import MilvusStore
from common import BotConfig


class FakeEmbedder:
    """按内容返回确定性向量的假嵌入器（dim=4）。"""

    def __init__(self) -> None:
        self.queries: list[str] = []

    async def embed_query(self, query: str) -> list[float]:
        self.queries.append(query)
        return [1.0, 0.0, 0.0, 0.0]

    async def embed_documents(self, contents: list[str]) -> list[list[float]]:
        vecs = []
        for c in contents:
            if "嵌入" in c:
                vecs.append([0.9, 0.1, 0.0, 0.0])
            elif "另一" in c:
                vecs.append([0.0, 0.9, 0.0, 0.0])
            else:
                vecs.append([0.1, 0.1, 0.0, 0.0])
        return vecs

    def close(self) -> None:
        pass


def _meta(thread_id, sender, content, ts, receiver=""):
    return {
        "thread_id": thread_id, "sender_id": sender, "sender_name": sender,
        "receiver_id": receiver or "", "receiver_name": receiver,
        "content": content, "timestamp": ts,
    }


def _store(tmp_path, retention=100) -> MilvusStore:
    return MilvusStore(
        BotConfig(embed_dimensions=4, rag_retention_per_thread=retention),
        uri=str(tmp_path / "milvus.db"),
        embedder=FakeEmbedder(),
    )


def test_dense_search_returns_top_hit_with_score(tmp_path):
    store = _store(tmp_path)
    asyncio.run(store.add_texts(
        ["关于嵌入模型的讨论", "另一话题"],
        [_meta("g1", "张三", "关于嵌入模型的讨论", "2026-08-01 10:00:00"),
         _meta("g1", "李四", "另一话题", "2026-08-01 11:00:00")],
    ))
    hits = asyncio.run(store.search_dense("嵌入", "", "g1", k=5))
    assert hits[0]["content"] == "关于嵌入模型的讨论"
    assert hits[0]["score"] > 0.8  # 1 - cosine_distance([1,0,0,0],[0.9,0.1,0,0]) ≈ 0.99
    assert hits[0]["sender_name"] == "张三"


def test_dense_thread_isolation_by_expr(tmp_path):
    store = _store(tmp_path)
    asyncio.run(store.add_texts(
        ["关于嵌入模型的讨论", "关于嵌入模型的讨论"],
        [_meta("g1", "张三", "关于嵌入模型的讨论", "2026-08-01 10:00:00"),
         _meta("g2", "王五", "关于嵌入模型的讨论", "2026-08-02 12:00:00")],
    ))
    hits = asyncio.run(store.search_dense("嵌入", "", "g1", k=5))
    assert {h["thread_id"] for h in hits} == {"g1"}


def test_sparse_search_by_chinese_keyword(tmp_path):
    store = _store(tmp_path)
    asyncio.run(store.add_texts(
        ["张三说今晚去吃火锅", "李四在讨论别的事"],
        [_meta("g1", "张三", "张三说今晚去吃火锅", "2026-08-01 10:00:00"),
         _meta("g1", "李四", "李四在讨论别的事", "2026-08-01 11:00:00")],
    ))
    hits = asyncio.run(store.search_sparse("火锅", "", "g1", k=5))
    assert hits and "火锅" in hits[0]["content"]


def test_sparse_search_no_match_returns_empty(tmp_path):
    store = _store(tmp_path)
    asyncio.run(store.add_texts(
        ["张三说今晚去吃火锅"],
        [_meta("g1", "张三", "张三说今晚去吃火锅", "2026-08-01 10:00:00")],
    ))
    hits = asyncio.run(store.search_sparse("量子计算", "", "g1", k=5))
    assert hits == []


def test_expr_person_filter(tmp_path):
    store = _store(tmp_path)
    asyncio.run(store.add_texts(
        ["张三说今晚去吃火锅", "李四说今晚去吃火锅"],
        [_meta("g1", "张三", "张三说今晚去吃火锅", "2026-08-01 10:00:00"),
         _meta("g1", "李四", "李四说今晚去吃火锅", "2026-08-01 11:00:00")],
    ))
    # 跨群（thread_id=None）+ 人名前缀过滤
    hits = asyncio.run(store.search_sparse("火锅", "sender_name like '张%'", None, k=5))
    assert len(hits) == 1
    assert hits[0]["sender_name"] == "张三"


def test_expr_time_window(tmp_path):
    store = _store(tmp_path)
    asyncio.run(store.add_texts(
        ["旧消息关于嵌入模型", "新消息关于嵌入模型"],
        [_meta("g1", "张三", "旧消息关于嵌入模型", "2026-07-01 10:00:00"),
         _meta("g1", "张三", "新消息关于嵌入模型", "2026-08-01 10:00:00")],
    ))
    hits = asyncio.run(store.search_dense(
        "嵌入", "timestamp >= '2026-08-01 00:00:00'", "g1", k=5,
    ))
    assert len(hits) == 1
    assert hits[0]["content"] == "新消息关于嵌入模型"


def test_add_texts_enforces_retention(tmp_path):
    store = _store(tmp_path, retention=2)
    asyncio.run(store.add_texts(
        ["m1", "m2", "m3"],
        [_meta("g1", "张三", "m1", "2026-08-01 10:00:00"),
         _meta("g1", "张三", "m2", "2026-08-01 11:00:00"),
         _meta("g1", "张三", "m3", "2026-08-01 12:00:00")],
    ))
    hits = asyncio.run(store.search_dense("m", "", "g1", k=10))
    # 3 条按 timestamp DESC 淘汰到 2 条（保留最新）
    assert {h["content"] for h in hits} == {"m2", "m3"}


def test_prune_keeps_newest(tmp_path):
    store = _store(tmp_path, retention=100)
    asyncio.run(store.add_texts(
        ["m1", "m2", "m3"],
        [_meta("g1", "张三", "m1", "2026-08-01 10:00:00"),
         _meta("g1", "张三", "m2", "2026-08-01 11:00:00"),
         _meta("g1", "张三", "m3", "2026-08-01 12:00:00")],
    ))
    store.prune("g1", 2)
    hits = asyncio.run(store.search_dense("m", "", "g1", k=10))
    assert {h["content"] for h in hits} == {"m2", "m3"}
