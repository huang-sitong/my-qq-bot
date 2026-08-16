"""RRF（Reciprocal Rank Fusion）合并多个按相关性排序的候选列表。

score(doc) = Σ 1/(k + rank_i)，rank 从 1 开始，k 默认 60（经典 RRF 常数）。
跨信号重复的 doc（如 dense+sparse 双命中）分数叠加、只返回一次；按分数
降序返回，同分按首次出现的 rank 升序（稳定、确定性）。候选 dict 必须含
``"id"`` 主键（Milvus search hit 的 pk）用于去重。
"""

RRF_K = 60


def rrf_merge(ranked_lists: list[list[dict]], k: int = RRF_K) -> list[dict]:
    """把多路按相关性排序的候选列表融合成一份去重后的排名列表。"""
    scores: dict = {}
    first_rank: dict = {}
    doc_by_id: dict = {}
    for ranked in ranked_lists:
        for rank, hit in enumerate(ranked, start=1):
            doc_id = hit["id"]
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
            first_rank.setdefault(doc_id, rank)
            doc_by_id.setdefault(doc_id, hit)
    merged = sorted(scores, key=lambda doc_id: (-scores[doc_id], first_rank[doc_id]))
    return [doc_by_id[doc_id] for doc_id in merged]
