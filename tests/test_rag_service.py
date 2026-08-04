"""RagService.index_turn：空内容配对过滤（非回复轮只索引用户消息 1 条）。

注入 fake embedder + recording store，避免真实 RagService 落到 Ollama/建库路径。
"""

import asyncio
import re
from datetime import datetime, timedelta

from bot.core.rag.service import RagService, TS_FMT
from common import BotConfig

ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")


class RecordingStore:
    """记录 add / query_meta 调用的假向量存储。"""

    def __init__(self) -> None:
        self.added: list[list[dict]] = []
        self.query_calls: list[dict] = []

    def add(self, records: list[dict]) -> None:
        self.added.append(records)

    def query_meta(self, thread_id, person="", content_keyword="",
                   since_iso="", until_iso="", limit=10) -> list[dict]:
        self.query_calls.append({
            "thread_id": thread_id, "person": person,
            "content_keyword": content_keyword,
            "since_iso": since_iso, "until_iso": until_iso, "limit": limit,
        })
        return []

    def close(self) -> None:
        pass


class FakeEmbedder:
    """返回固定向量的假嵌入器。"""

    def __init__(self) -> None:
        self.calls = 0

    async def embed_documents(self, contents: list[str]) -> list[list[float]]:
        self.calls += 1
        return [[0.1] * 4 for _ in contents]

    def close(self) -> None:
        pass


def _svc(embedder: FakeEmbedder, store: RecordingStore) -> RagService:
    return RagService(
        BotConfig(rag_enabled=True, embed_dimensions=4),
        embedder=embedder,
        store=store,
    )


def test_index_turn_with_reply_indexes_two():
    store = RecordingStore()
    embedder = FakeEmbedder()
    svc = _svc(embedder, store)
    asyncio.run(svc.index_turn("t", "u1", "张三", "bot1", "小助手", "晚上吃什么", "去吃火锅"))

    assert len(store.added) == 1
    records = store.added[0]
    assert len(records) == 2
    # 用户消息：sender=用户，receiver=bot
    assert records[0]["sender_id"] == "u1"
    assert records[0]["sender_name"] == "张三"
    assert records[0]["receiver_id"] == "bot1"
    assert records[0]["receiver_name"] == "小助手"
    assert records[0]["content"] == "晚上吃什么"
    # bot 回复：sender=bot，receiver=用户
    assert records[1]["sender_id"] == "bot1"
    assert records[1]["sender_name"] == "小助手"
    assert records[1]["receiver_id"] == "u1"
    assert records[1]["receiver_name"] == "张三"
    assert records[1]["content"] == "去吃火锅"
    # timestamp 落库为 ISO 字符串（YYYY-MM-DD HH:MM:SS），非 epoch 整数
    for rec in records:
        assert ISO_RE.match(rec["timestamp"])


def test_index_turn_without_reply_indexes_only_user():
    store = RecordingStore()
    embedder = FakeEmbedder()
    svc = _svc(embedder, store)
    asyncio.run(svc.index_turn("t", "u1", "张三", "bot1", "小助手", "晚上吃什么", ""))

    assert len(store.added) == 1
    records = store.added[0]
    assert len(records) == 1
    assert records[0]["sender_name"] == "张三"
    assert records[0]["receiver_name"] == ""  # 非回复轮（群广播），无特定接收者
    assert records[0]["content"] == "晚上吃什么"


def test_index_turn_all_empty_is_noop():
    store = RecordingStore()
    embedder = FakeEmbedder()
    svc = _svc(embedder, store)
    asyncio.run(svc.index_turn("t", "u1", "张三", "bot1", "小助手", "", ""))

    assert store.added == []  # 无记录入库
    assert embedder.calls == 0  # 未触发嵌入


def test_index_turn_empty_user_but_reply_sender_is_bot():
    store = RecordingStore()
    embedder = FakeEmbedder()
    svc = _svc(embedder, store)
    asyncio.run(svc.index_turn("t", "u1", "张三", "bot1", "小助手", "", "这是回复"))

    assert len(store.added) == 1
    records = store.added[0]
    assert len(records) == 1
    assert records[0]["sender_name"] == "小助手"  # bot 回复，sender 是 bot
    assert records[0]["receiver_name"] == "张三"
    assert records[0]["content"] == "这是回复"


def test_index_turn_disabled_is_noop():
    store = RecordingStore()
    svc = RagService(
        BotConfig(rag_enabled=False, embed_dimensions=4),
        embedder=FakeEmbedder(),
        store=store,
    )
    asyncio.run(svc.index_turn("t", "u1", "张三", "bot1", "小助手", "你好", "在的"))

    assert store.added == []


def test_search_by_user_hours_sets_iso_since():
    store = RecordingStore()
    svc = _svc(FakeEmbedder(), store)
    asyncio.run(svc.search_by_user("t", person="张三", hours=24))

    assert len(store.query_calls) == 1
    call = store.query_calls[0]
    assert call["person"] == "张三"
    assert call["since_iso"] and call["until_iso"] == ""
    since = datetime.strptime(call["since_iso"], TS_FMT)
    elapsed = (datetime.now() - since).total_seconds()
    assert 23 * 3600 < elapsed < 25 * 3600  # ≈ 24h 前，ISO 规范格式


def test_search_by_user_explicit_window_wins_over_hours():
    store = RecordingStore()
    svc = _svc(FakeEmbedder(), store)
    asyncio.run(svc.search_by_user(
        "t", start_time="2026-07-01 00:00:00", end_time="2026-08-01 23:59:59", hours=24))

    call = store.query_calls[0]
    assert call["since_iso"] == "2026-07-01 00:00:00"  # 显式 start 不被 hours 覆盖
    assert call["until_iso"] == "2026-08-01 23:59:59"
