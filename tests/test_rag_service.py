"""RagService 编排测试：index_turn 配对、hybrid_search 委托 / 阈值 / 跨群补齐。

注入 FakeMilvusStore（记录调用），不碰真实 milvus / Ollama。
"""

import asyncio
from datetime import datetime

from bot.core.rag.service import RagService, TS_FMT
from common import BotConfig


class FakeMilvusStore:
    """记录 add_texts / search_dense / search_sparse 调用的假存储。

    scripted: 可注入脚本化命中，供阈值过滤 / 跨群补齐测试。
    """

    def __init__(self, dense_hits=None, sparse_hits=None) -> None:
        self.added: list[tuple[list[str], list[dict]]] = []
        self.dense_calls: list[dict] = []
        self.sparse_calls: list[dict] = []
        self.dense_hits = dense_hits or []
        self.sparse_hits = sparse_hits or []

    async def add_texts(self, texts: list[str], metadatas: list[dict]) -> None:
        self.added.append((texts, metadatas))

    async def search_dense(self, query, expr, thread_id, k):
        self.dense_calls.append({"query": query, "expr": expr, "thread_id": thread_id, "k": k})
        return list(self.dense_hits)

    async def search_sparse(self, query, expr, thread_id, k):
        self.sparse_calls.append({"query": query, "expr": expr, "thread_id": thread_id, "k": k})
        return list(self.sparse_hits)

    def close(self) -> None:
        pass


def _svc(store: FakeMilvusStore) -> RagService:
    return RagService(
        BotConfig(rag_enabled=True, rag_top_k=5, rag_score_threshold=0.5),
        store=store,
    )


def _hit(doc_id, content, thread_id="g1", score=0.9, sender="张三"):
    return {
        "id": doc_id, "thread_id": thread_id, "sender_id": "u1", "sender_name": sender,
        "receiver_id": "", "receiver_name": "", "content": content,
        "timestamp": "2026-08-01 10:00:00", "score": score,
    }


# --- index_turn：配对过滤（与旧语义一致） ----------------------------------

def test_index_turn_with_reply_indexes_two():
    store = FakeMilvusStore()
    svc = _svc(store)
    asyncio.run(svc.index_turn("t", "u1", "张三", "bot1", "小助手", "晚上吃什么", "去吃火锅"))

    assert len(store.added) == 1
    texts, metas = store.added[0]
    assert texts == ["晚上吃什么", "去吃火锅"]
    assert metas[0]["sender_id"] == "u1"
    assert metas[0]["sender_name"] == "张三"
    assert metas[0]["receiver_id"] == "bot1"
    assert metas[0]["receiver_name"] == "小助手"
    assert metas[1]["sender_id"] == "bot1"
    assert metas[1]["sender_name"] == "小助手"
    assert metas[1]["receiver_id"] == "u1"
    assert metas[1]["receiver_name"] == "张三"
    # content 动态字段与文本一致（search 输出与工具渲染依赖它）
    assert metas[0]["content"] == "晚上吃什么"
    assert metas[1]["content"] == "去吃火锅"


def test_index_turn_without_reply_indexes_only_user():
    store = FakeMilvusStore()
    svc = _svc(store)
    asyncio.run(svc.index_turn("t", "u1", "张三", "bot1", "小助手", "晚上吃什么", ""))

    texts, metas = store.added[0]
    assert texts == ["晚上吃什么"]
    assert metas[0]["receiver_name"] == ""  # 非回复轮（群广播），无特定接收者


def test_index_turn_all_empty_is_noop():
    store = FakeMilvusStore()
    svc = _svc(store)
    asyncio.run(svc.index_turn("t", "u1", "张三", "bot1", "小助手", "", ""))
    assert store.added == []


def test_index_turn_disabled_is_noop():
    store = FakeMilvusStore()
    svc = RagService(BotConfig(rag_enabled=False), store=store)
    asyncio.run(svc.index_turn("t", "u1", "张三", "bot1", "小助手", "你好", "在的"))
    assert store.added == []


# --- hybrid_search：委托 / 阈值 / 补齐 -------------------------------------

def test_search_calls_dense_and_sparse():
    # 5 条本群命中（≥ top_k=5）→ 不触发跨群补齐，恰好各 1 次 dense/sparse
    store = FakeMilvusStore(
        dense_hits=[_hit(i, f"命中{i}") for i in range(5)],
    )
    svc = _svc(store)
    hits = asyncio.run(svc.search("嵌入模型", "t"))
    assert len(store.dense_calls) == 1
    assert store.dense_calls[0]["query"] == "嵌入模型"
    assert store.dense_calls[0]["thread_id"] == "t"
    assert store.dense_calls[0]["k"] == 50  # CANDIDATE_K
    assert store.sparse_calls[0]["query"] == "嵌入模型"  # query 兼作 sparse 信号
    assert hits[0]["content"] == "命中0"


def test_search_dense_below_threshold_filtered():
    store = FakeMilvusStore(dense_hits=[_hit(1, "低分命中", score=0.2)])
    svc = _svc(store)
    hits = asyncio.run(svc.search("嵌入模型", "t"))
    assert hits == []  # score 0.2 < rag_score_threshold 0.5 → 被滤掉


def test_search_cross_guild_fill_when_insufficient():
    store = FakeMilvusStore(dense_hits=[], sparse_hits=[])
    svc = _svc(store)
    asyncio.run(svc.search("嵌入模型", "t"))
    # 本群 0 命中 < top_k → 触发跨群补齐（thread_id=None）
    assert len(store.dense_calls) == 2
    assert store.dense_calls[1]["thread_id"] is None
    assert store.sparse_calls[1]["thread_id"] is None


def test_search_no_fill_when_in_thread_already_enough():
    store = FakeMilvusStore(
        dense_hits=[_hit(i, f"命中{i}", score=0.9) for i in range(5)],
    )
    svc = _svc(store)
    hits = asyncio.run(svc.search("嵌入模型", "t"))
    assert len(store.dense_calls) == 1  # 5 条 ≥ top_k=5 → 不跨群
    assert len(hits) == 5


def test_search_by_user_person_only_returns_empty():
    store = FakeMilvusStore()
    svc = _svc(store)
    hits = asyncio.run(svc.search_by_user(None, person="张三"))
    assert hits == []  # person-only：无 content/query → 跳过双信号
    assert store.dense_calls == []
    assert store.sparse_calls == []


def test_search_by_user_content_keyword_sparse_only():
    store = FakeMilvusStore(sparse_hits=[_hit(1, "谁说过qwen3")])
    svc = _svc(store)
    hits = asyncio.run(svc.search_by_user(None, content_keyword="qwen3"))
    assert store.dense_calls == []  # query 空 → 跳过 dense
    assert store.sparse_calls[0]["query"] == "qwen3"
    assert store.sparse_calls[0]["thread_id"] is None  # 跨全部群
    assert hits[0]["content"] == "谁说过qwen3"


def test_search_by_user_person_enters_expr():
    store = FakeMilvusStore(sparse_hits=[_hit(1, "张三说今晚吃火锅")])
    svc = _svc(store)
    asyncio.run(svc.search_by_user(None, person="张三", content_keyword="火锅"))
    expr = store.sparse_calls[0]["expr"]
    assert "sender_name like '张三%'" in expr
    assert "receiver_name like '张三%'" in expr


def test_search_hours_sets_iso_since_in_expr():
    store = FakeMilvusStore()
    svc = _svc(store)
    asyncio.run(svc.search("嵌入模型", "t", hours=24))
    expr = store.dense_calls[0]["expr"]
    assert expr.startswith("timestamp >= '")
    since = expr.split("'")[1]
    elapsed = (datetime.now() - datetime.strptime(since, TS_FMT)).total_seconds()
    assert 23 * 3600 < elapsed < 25 * 3600  # ≈ 24h 前


def test_search_by_user_explicit_window_wins_over_hours():
    store = FakeMilvusStore(sparse_hits=[_hit(1, "x")])
    svc = _svc(store)
    asyncio.run(svc.search_by_user(
        None, content_keyword="x",
        start_time="2026-07-01 00:00:00", end_time="2026-08-01 23:59:59", hours=24,
    ))
    expr = store.sparse_calls[0]["expr"]
    assert "timestamp >= '2026-07-01 00:00:00'" in expr
    assert "timestamp <= '2026-08-01 23:59:59'" in expr


def test_search_failure_returns_empty():
    class Boom(FakeMilvusStore):
        async def search_dense(self, query, expr, thread_id, k):
            raise RuntimeError("milvus down")

    svc = _svc(Boom())
    hits = asyncio.run(svc.search("嵌入模型", "t"))
    assert hits == []


def test_index_turn_empty_user_but_reply_sender_is_bot():
    store = FakeMilvusStore()
    svc = _svc(store)
    asyncio.run(svc.index_turn("t", "u1", "张三", "bot1", "小助手", "", "这是回复"))

    assert len(store.added) == 1
    texts, metas = store.added[0]
    assert texts == ["这是回复"]
    assert metas[0]["sender_name"] == "小助手"  # bot 回复，sender 是 bot
    assert metas[0]["receiver_name"] == "张三"


def test_search_by_user_explicit_thread_does_not_cross_guild():
    store = FakeMilvusStore(sparse_hits=[])
    svc = _svc(store)
    asyncio.run(svc.search_by_user("g1", content_keyword="x"))
    # query 空 → 不触发跨群补齐（限定单群契约）
    assert len(store.sparse_calls) == 1
    assert store.sparse_calls[0]["thread_id"] == "g1"


# --- 端到端：真实 MilvusStore 的 index_turn → search 闭环 -------------------

def test_index_turn_then_search_returns_content(tmp_path):
    """回归测试：index_turn 写入的动态 content 字段在检索命中里可读。

    若 metadata 缺 content，search 返回的 hit 无该键，工具渲染会 KeyError
    降级为「工具执行失败。」（库行里 text 仅作 BM25 输入，不带出检索结果）。
    """
    from bot.core.rag.milvus import MilvusStore

    class FE:
        """确定性假嵌入器（dim=4），query 向量指向含"嵌入"的文档。"""

        async def embed_query(self, query):
            return [1.0, 0.0, 0.0, 0.0]

        async def embed_documents(self, contents):
            return [
                [0.9, 0.1, 0.0, 0.0] if "嵌入" in c else [0.1, 0.1, 0.0, 0.0]
                for c in contents
            ]

        def close(self):
            pass

    store = MilvusStore(
        BotConfig(embed_dimensions=4, rag_retention_per_thread=100),
        uri=str(tmp_path / "milvus.db"), embedder=FE(),
    )
    svc = RagService(
        BotConfig(rag_enabled=True, rag_top_k=5, rag_score_threshold=0.35),
        store=store,
    )
    asyncio.run(svc.index_turn(
        "g1", "u1", "张三", "bot1", "小助手", "关于嵌入模型的讨论", "这是回复",
    ))
    hits = asyncio.run(svc.search("嵌入", "g1"))
    assert any(h["content"] == "关于嵌入模型的讨论" for h in hits)
    store.close()
