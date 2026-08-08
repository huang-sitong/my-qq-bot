"""查看 db/milvus.db（milvus-lite）里的聊天记录向量数据。

用法（在项目根目录 F:\\PythonProject\\qq-bot 下运行）：
    uv run python scripts/inspect_milvus.py                 # 列出全部记录（默认）
    uv run python scripts/inspect_milvus.py --schema        # 查看集合结构
    uv run python scripts/inspect_milvus.py --stats         # 按群统计条数
    uv run python scripts/inspect_milvus.py --thread llonebot:796219047:796219047   # 只看某个群
    uv run python scripts/inspect_milvus.py --limit 20      # 限制条数
    uv run python scripts/inspect_milvus.py --keyword 火锅  # 内容关键词过滤（Python 侧子串匹配）
    uv run python scripts/inspect_milvus.py --pk 1 --full   # 查看单条完整记录（含 vector/sparse 统计）

注意：milvus.db 不是 sqlite，必须用 pymilvus 读；且 bot 运行时持有 LOCK，请先停止 bot。
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter

from pymilvus import MilvusClient

# 可读字段（排除 vector / sparse 两个高维向量字段，默认不打印）
READABLE_FIELDS = [
    "pk",
    "thread_id",
    "sender_id",
    "sender_name",
    "receiver_id",
    "receiver_name",
    "content",
    "timestamp",
]

COLLECTION = "chat"


def get_client(uri: str) -> MilvusClient:
    return MilvusClient(uri=uri)


def show_schema(client: MilvusClient) -> None:
    desc = client.describe_collection(COLLECTION)
    print("collection:", desc.get("collection_name"))
    print("num_entities:", desc.get("num_entities"))
    print("fields:")
    for f in desc.get("fields", []):
        params = f.get("params") or {}
        extra = ""
        if f.get("is_primary"):
            extra += " primary"
        if f.get("is_partition_key"):
            extra += " partition_key"
        if "dim" in params:
            extra += f" dim={params['dim']}"
        if f.get("enable_analyzer"):
            extra += " analyzer"
        print(f"  {f.get('name')}: {f.get('type')}{extra}")


def show_stats(client: MilvusClient) -> None:
    rows = client.query(
        collection_name=COLLECTION,
        filter="pk >= 0",
        output_fields=["thread_id"],
    )
    counter = Counter(r["thread_id"] for r in rows)
    print(f"total: {len(rows)}")
    for tid, n in sorted(counter.items(), key=lambda x: -x[1]):
        print(f"  {n:>5}  {tid}")


def list_rows(client: MilvusClient, thread: str | None, limit: int, keyword: str | None) -> None:
    rows = client.query(
        collection_name=COLLECTION,
        filter="pk >= 0",
        output_fields=READABLE_FIELDS,
    )
    if thread:
        rows = [r for r in rows if r.get("thread_id") == thread]
    if keyword:
        rows = [r for r in rows if keyword in (r.get("content") or "")]
    rows.sort(key=lambda r: (r.get("thread_id") or "", r.get("timestamp") or "", r.get("pk", 0)))
    if limit:
        rows = rows[:limit]
    print(f"total shown: {len(rows)}")
    for r in rows:
        content = (r.get("content") or "").replace("\n", "\\n")
        print(
            f"[pk={r.get('pk')}] {r.get('timestamp')} "
            f"{r.get('sender_name')} -> {r.get('receiver_name')} "
            f"| {content[:80]} | {r.get('thread_id')}"
        )


def show_one(client: MilvusClient, pk: int, full: bool) -> None:
    rows = client.query(
        collection_name=COLLECTION,
        filter=f"pk == {pk}",
        output_fields=None if full else READABLE_FIELDS,
    )
    if not rows:
        print(f"no row with pk={pk}")
        return
    row = rows[0]
    if full:
        # 向量字段单独展示，避免刷屏
        for k in ("vector", "sparse"):
            if k in row:
                vec = row[k]
                if isinstance(vec, dict):
                    n = len(vec)
                else:
                    n = len(vec) if hasattr(vec, "__len__") else "?"
                print(f"  {k}: {n} dims")
                row.pop(k, None)
    print(row)


def main() -> None:
    parser = argparse.ArgumentParser(description="查看 milvus-lite 聊天记录向量库")
    parser.add_argument("--uri", default="db/milvus.db", help="milvus 库路径（默认 db/milvus.db）")
    parser.add_argument("--schema", action="store_true", help="显示集合结构")
    parser.add_argument("--stats", action="store_true", help="按群统计条数")
    parser.add_argument("--thread", help="只看指定 thread_id（如 llonebot:796219047:796219047）")
    parser.add_argument("--limit", type=int, default=0, help="限制展示条数（默认全部）")
    parser.add_argument("--keyword", help="内容关键词过滤（子串匹配）")
    parser.add_argument("--pk", type=int, help="查看指定主键的记录")
    parser.add_argument("--full", action="store_true", help="--pk 时显示完整记录（含向量维度统计）")
    args = parser.parse_args()

    client = get_client(args.uri)
    if args.schema:
        show_schema(client)
    elif args.stats:
        show_stats(client)
    elif args.pk is not None:
        show_one(client, args.pk, args.full)
    else:
        list_rows(client, args.thread, args.limit, args.keyword)


if __name__ == "__main__":
    # Windows PowerShell 中文输出兜底
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
