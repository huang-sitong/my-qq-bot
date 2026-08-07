"""RagVectorStore 存储/检索集成测试（真实 sqlite-vec，tmp_path 临时库）。

覆盖：
- search 候选一次 JOIN 批量取回（回归：逐行 _fetch_meta 的 N+1 查询）
- 语义检索当前群优先、本群命中不足时跨群补齐
- query_meta 跨群（thread_id=None 检索全部群）与单群过滤
- 时间窗口在语义检索的候选剪枝
"""

from bot.core.rag.store import RagVectorStore


def _store(tmp_path, dimensions=4) -> RagVectorStore:
    return RagVectorStore(
        db_dir=str(tmp_path), dimensions=dimensions,
        retention_per_thread=100, candidate_k=50,
    )


def _rec(thread_id, sender, receiver, content, ts, vec):
    return {
        "thread_id": thread_id, "sender_id": sender, "sender_name": sender,
        "receiver_id": receiver, "receiver_name": receiver,
        "content": content, "timestamp": ts, "embedding": vec,
    }


def test_search_batches_meta_fetch(tmp_path):
    store = _store(tmp_path)
    store.add([
        _rec("g1", "张三", "", "关于嵌入模型的讨论", "2026-08-01 10:00:00", [0.9, 0.0, 0.0, 0.0]),
        _rec("g1", "李四", "", "另一个话题", "2026-08-01 11:00:00", [0.0, 0.9, 0.0, 0.0]),
        _rec("g2", "王五", "", "跨群的记录", "2026-08-02 12:00:00", [0.9, 0.1, 0.0, 0.0]),
    ])
    hits = store.search([1.0, 0.0, 0.0, 0.0], "g1", top_k=5, score_threshold=0.5)
    assert hits[0]["content"] == "关于嵌入模型的讨论"
    assert hits[0]["score"] > 0.8  # 余弦相似度 ≈ 0.9


def test_search_cross_thread_fill_when_same_thread_insufficient(tmp_path):
    store = _store(tmp_path)
    store.add([
        _rec("g1", "张三", "", "本地消息", "2026-08-01 10:00:00", [0.9, 0.0, 0.0, 0.0]),
        _rec("g2", "王五", "", "远群的相关消息", "2026-08-02 12:00:00", [0.8, 0.1, 0.0, 0.0]),
    ])
    hits = store.search([1.0, 0.0, 0.0, 0.0], "g1", top_k=2, score_threshold=0.5)
    assert [h["thread_id"] for h in hits] == ["g1", "g2"]  # 当前群优先，不足跨群补齐


def test_query_meta_none_thread_scans_all_groups(tmp_path):
    store = _store(tmp_path)
    store.add([
        _rec("g1", "张三", "小助手", "A群发言", "2026-08-01 10:00:00", [0.1] * 4),
        _rec("g2", "张三", "小助手", "B群发言", "2026-08-02 11:00:00", [0.1] * 4),
    ])
    all_hits = store.query_meta(None, person="张三")
    assert {h["thread_id"] for h in all_hits} == {"g1", "g2"}

    g1_only = store.query_meta("g1", person="张三")
    assert [h["thread_id"] for h in g1_only] == ["g1"]


def test_query_meta_content_keyword_and_time_window(tmp_path):
    store = _store(tmp_path)
    store.add([
        _rec("g1", "张三", "", "讨论 qwen3-embedding", "2026-08-01 10:00:00", [0.1] * 4),
        _rec("g1", "李四", "", "闲聊", "2026-08-05 10:00:00", [0.1] * 4),
    ])
    hits = store.query_meta(
        None, content_keyword="qwen3",
        since_iso="2026-08-01 00:00:00", until_iso="2026-08-31 23:59:59",
    )
    assert len(hits) == 1
    assert hits[0]["content"] == "讨论 qwen3-embedding"


def test_search_time_window_prunes_candidates(tmp_path):
    store = _store(tmp_path)
    store.add([
        _rec("g1", "张三", "", "旧消息", "2026-07-01 10:00:00", [0.9, 0.0, 0.0, 0.0]),
        _rec("g1", "张三", "", "新消息", "2026-08-01 10:00:00", [0.9, 0.0, 0.0, 0.0]),
    ])
    hits = store.search(
        [1.0, 0.0, 0.0, 0.0], "g1", top_k=5, score_threshold=0.5,
        since_iso="2026-08-01 00:00:00",
    )
    assert len(hits) == 1
    assert hits[0]["content"] == "新消息"
