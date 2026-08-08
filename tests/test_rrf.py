"""RRF（Reciprocal Rank Fusion）合并单测：toy rankings、k=60、跨信号叠加、去重。"""

from bot.core.rag.rrf import RRF_K, rrf_merge


def _h(doc_id, **extra):
    return {"id": doc_id, **extra}


def test_rrf_empty_inputs():
    assert rrf_merge([[], []]) == []


def test_rrf_single_list_preserves_order():
    a = [_h(1), _h(2), _h(3)]
    assert [h["id"] for h in rrf_merge([a])] == [1, 2, 3]


def test_rrf_merges_two_lists_rank_order():
    a = [_h(1), _h(2)]
    b = [_h(3), _h(4)]
    merged = rrf_merge([a, b])
    # RRF 按分数分档：1/61 档(1,3) → 1/62 档(2,4)；同分按插入序（dense 在前）稳定排序
    assert [h["id"] for h in merged] == [1, 3, 2, 4]


def test_rrf_cross_signal_overlap_gets_higher_score():
    a = [_h(1), _h(2)]
    b = [_h(1), _h(3)]  # 1 双命中 → 分数叠加，应排第一
    merged = rrf_merge([a, b])
    assert [h["id"] for h in merged] == [1, 2, 3]


def test_rrf_dedups_by_id_returns_once():
    a = [_h(7, content="来自dense")]
    b = [_h(7, content="来自sparse")]
    merged = rrf_merge([a, b])
    assert len(merged) == 1
    assert merged[0]["id"] == 7


def test_rrf_rank_1_in_second_list_beats_rank_2_in_first():
    a = [_h(1), _h(2)]          # rank 1 → 1/61, rank 2 → 1/62
    b = [_h(3)]                  # rank 1 → 1/61
    merged = rrf_merge([a, b])
    # 1 和 3 并列 1/61，同分按首次出现 rank 升序 → 1 在前
    assert [h["id"] for h in merged] == [1, 3, 2]


def test_rrf_k_constant_is_60():
    assert RRF_K == 60
