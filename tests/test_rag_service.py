"""RagService.index_turn：空内容配对过滤（非回复轮只索引用户消息 1 条）。

注入 fake embedder + recording store，避免真实 RagService 落到 Ollama/建库路径。
"""

import asyncio

from bot.core.rag.service import RagService
from common import BotConfig


class RecordingStore:
    """记录 add 调用的假向量存储。"""

    def __init__(self) -> None:
        self.added: list[list[dict]] = []

    def add(self, records: list[dict]) -> None:
        self.added.append(records)

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
    asyncio.run(svc.index_turn("t", "u1", "张三", "晚上吃什么", "去吃火锅"))

    assert len(store.added) == 1
    records = store.added[0]
    assert len(records) == 2
    assert [r["role"] for r in records] == ["user", "assistant"]
    assert records[0]["content"] == "晚上吃什么"
    assert records[1]["content"] == "去吃火锅"


def test_index_turn_without_reply_indexes_only_user():
    store = RecordingStore()
    embedder = FakeEmbedder()
    svc = _svc(embedder, store)
    asyncio.run(svc.index_turn("t", "u1", "张三", "晚上吃什么", ""))

    assert len(store.added) == 1
    records = store.added[0]
    assert len(records) == 1
    assert records[0]["role"] == "user"
    assert records[0]["content"] == "晚上吃什么"


def test_index_turn_all_empty_is_noop():
    store = RecordingStore()
    embedder = FakeEmbedder()
    svc = _svc(embedder, store)
    asyncio.run(svc.index_turn("t", "u1", "张三", "", ""))

    assert store.added == []  # 无记录入库
    assert embedder.calls == 0  # 未触发嵌入


def test_index_turn_empty_user_but_reply_keeps_role():
    store = RecordingStore()
    embedder = FakeEmbedder()
    svc = _svc(embedder, store)
    asyncio.run(svc.index_turn("t", "u1", "张三", "", "这是回复"))

    assert len(store.added) == 1
    records = store.added[0]
    assert len(records) == 1
    assert records[0]["role"] == "assistant"  # 配对过滤不标错 role
    assert records[0]["content"] == "这是回复"


def test_index_turn_disabled_is_noop():
    store = RecordingStore()
    svc = RagService(
        BotConfig(rag_enabled=False, embed_dimensions=4),
        embedder=FakeEmbedder(),
        store=store,
    )
    asyncio.run(svc.index_turn("t", "u1", "张三", "你好", "在的"))

    assert store.added == []
