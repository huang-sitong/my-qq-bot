"""MilvusStore 集成测试（真实 milvus-lite，tmp_path 临时库）。

覆盖：add_texts 写库、search_dense / search_sparse、线程隔离（expr）、
expr 过滤（人名 / 时间窗）、prune 淘汰、dense score 语义。
测试注入 FakeEmbedder（确定性向量），不连真实嵌入 API。
"""

import asyncio
import threading

from bot.package.config import BotConfig
from bot.package.knowledge.milvus import MilvusStore


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


class _ImmediateEmbedder:
    async def embed_query(self, query):
        return [1.0, 0.0, 0.0, 0.0]

    async def embed_documents(self, contents):
        return [[1.0, 0.0, 0.0, 0.0] for _ in contents]

    def close(self):
        pass


class _FakeClient:
    def __init__(self):
        self.active = 0
        self.max_active = 0
        self.entered = threading.Event()
        self.release = threading.Event()

    def search(self, *args, **kwargs):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.entered.set()
        self.release.wait(2)
        self.active -= 1
        return [[]]

    def close(self):
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
    asyncio.run(store.prune("g1", 2))
    hits = asyncio.run(store.search_dense("m", "", "g1", k=10))
    assert {h["content"] for h in hits} == {"m2", "m3"}


def _build_db_in_subprocess(uri: str) -> None:
    """跨进程建带索引集合 + insert 后退出，模拟 bot 上次运行留下的库。

    milvus-lite 的 server 是进程内单例：带索引集合在**新进程**打开时默认
    ``released``（load 状态不持久化），query/search 前必须 ``load_collection``。
    """
    import subprocess
    import sys
    import textwrap

    script = textwrap.dedent(f"""
        from pymilvus import DataType, Function, FunctionType, MilvusClient
        c = MilvusClient({uri!r})
        schema = c.create_schema(auto_id=True, enable_dynamic_field=True)
        schema.add_field("pk", DataType.INT64, is_primary=True)
        schema.add_field("vector", DataType.FLOAT_VECTOR, dim=4)
        schema.add_field("sparse", DataType.SPARSE_FLOAT_VECTOR)
        schema.add_field("text", DataType.VARCHAR, max_length=65535,
                         enable_analyzer=True, analyzer_params={{"tokenizer": "jieba"}})
        schema.add_field("thread_id", DataType.VARCHAR, max_length=128, is_partition_key=True)
        schema.add_function(Function(name="bm25_fn", function_type=FunctionType.BM25,
                                     input_field_names=["text"], output_field_names=["sparse"]))
        ip = c.prepare_index_params()
        ip.add_index("vector", index_type="HNSW", metric_type="COSINE",
                     params={{"M": 8, "efConstruction": 64}})
        ip.add_index("sparse", index_type="SPARSE_INVERTED_INDEX", metric_type="BM25")
        c.create_collection("chat", schema=schema, index_params=ip)
        c.insert("chat", [{{"vector": [0.1] * 4, "text": "跨进程遗留的消息", "thread_id": "g1",
                            "sender_id": "张三", "sender_name": "张三",
                            "receiver_id": "", "receiver_name": "",
                            "content": "跨进程遗留的消息", "timestamp": "2026-08-01 10:00:00"}}])
        c.close()
    """)
    subprocess.run([sys.executable, "-c", script], timeout=120, check=True)


def test_reopen_collection_after_restart(tmp_path):
    """跨进程重启：新进程复用已存在的带索引集合，必须 load 才能 search。

    进程 A（subprocess）建库后退出 → 本进程（新进程）打开复用集合。若无
    ``_ensure_collection`` 里的 ``load_collection``，search 会抛 released 被
    RagService 降级为空 → hits 为空断言失败。
    """
    uri = str(tmp_path / "milvus.db")
    _build_db_in_subprocess(uri)
    store = MilvusStore(
        BotConfig(embed_dimensions=4, rag_retention_per_thread=100),
        uri=uri, embedder=FakeEmbedder(),
    )
    hits = asyncio.run(store.search_dense("遗留", "", "g1", k=5))
    assert hits and hits[0]["content"] == "跨进程遗留的消息"
    store.close()


def test_milvus_store_sets_safe_keepalive(tmp_path, monkeypatch):
    """MilvusStore 必须用 grpc_options 覆盖 pymilvus 硬编码的激进 keepalive。

    pymilvus 默认 ``grpc.keepalive_time_ms=10000`` + ``keepalive_permit_without_calls=True``，
    空闲时每 10s 发 keepalive ping；而 milvus-lite 进程内 server 用 gRPC 默认 ping 策略
    （无数据间隔 5 分钟，2 次违规即 ``too_many_pings`` GOAWAY）。bot 回合间空闲 45-120s，
    连接被 server 反复掐断（每轮刷屏 + 有 RPC 被静默丢的风险）。此处断言覆盖生效：
    idle 不主动 ping（permit_without_calls=False）且间隔 >= 5 分钟。
    """
    import pymilvus

    captured: dict = {}

    class RecordingClient(pymilvus.MilvusClient):
        def __init__(self, uri, **kwargs):
            captured["uri"] = uri
            captured["grpc_options"] = kwargs.get("grpc_options")
            super().__init__(uri, **kwargs)

    monkeypatch.setattr("bot.package.knowledge.milvus.MilvusClient", RecordingClient)
    store = _store(tmp_path)
    store.close()

    opts = captured.get("grpc_options") or {}
    assert opts.get("grpc.keepalive_permit_without_calls") is False, (
        "pymilvus 默认 permit_without_calls=True：空闲每 10s 发 ping，"
        "milvus-lite server 会发 too_many_pings GOAWAY 掐断连接"
    )
    assert int(opts.get("grpc.keepalive_time_ms", 0)) >= 300_000


def test_milvus_client_operations_serialized(tmp_path):
    store = _store(tmp_path)
    fake = _FakeClient()
    store._client = fake
    store._embedder = _ImmediateEmbedder()

    async def run():
        first = asyncio.create_task(store.search_dense("a", "", "g1", 1))
        await asyncio.to_thread(fake.entered.wait, 2)
        second = asyncio.create_task(store.search_sparse("b", "", "g1", 1))
        await asyncio.sleep(0.05)
        assert fake.max_active == 1
        fake.release.set()
        await asyncio.gather(first, second)

    asyncio.run(run())
