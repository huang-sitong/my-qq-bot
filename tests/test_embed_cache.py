"""EmbeddingCache + EmbeddingService 嵌入缓存：命中跳过 Ollama、批量、禁用、淘汰。"""

import asyncio

from bot.core.rag.cache import EmbeddingCache
from bot.core.rag.embedder import EmbeddingService
from common import BotConfig


class CountingEmbedder:
    """记录调用次数的假嵌入器。"""

    def __init__(self) -> None:
        self.query_calls = 0
        self.doc_calls = 0

    def embed_query(self, text: str) -> list[float]:
        self.query_calls += 1
        return [0.1] * 4

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.doc_calls += 1
        return [[0.1] * 4 for _ in texts]


def _svc(tmp_path, **cfg_overrides):
    config = BotConfig(embed_dimensions=4, **cfg_overrides)
    fake = CountingEmbedder()
    cache = EmbeddingCache(db_path=str(tmp_path / "cache.sqlite"))
    return EmbeddingService(config, embedder=fake, cache=cache), fake


# ----------------------------------------------------------------------
# EmbeddingCache 单元测试
# ----------------------------------------------------------------------

def test_cache_roundtrip(tmp_path):
    cache = EmbeddingCache(db_path=str(tmp_path / "c.sqlite"))
    assert cache.get("nope") is None
    cache.set("k1", "model-a", "text", [1.0, 2.0])
    assert cache.get("k1") == [1.0, 2.0]
    cache.close()


def test_cache_retention_evicts_oldest(tmp_path):
    cache = EmbeddingCache(db_path=str(tmp_path / "c.sqlite"), max_entries=3)
    for i in range(5):
        cache.set(f"k{i}", "m", f"text{i}", [float(i)])
    assert cache.count() == 3
    assert cache.get("k0") is None  # 最旧被淘汰
    assert cache.get("k4") == [4.0]  # 最新保留
    cache.close()


def test_cache_mget_miss_returns_none(tmp_path):
    cache = EmbeddingCache(db_path=str(tmp_path / "c.sqlite"))
    cache.set("a", "m", "ta", [1.0])
    assert cache.mget(["a", "b"]) == [[1.0], None]
    cache.close()


# ----------------------------------------------------------------------
# EmbeddingService 缓存行为
# ----------------------------------------------------------------------

def test_embed_query_hits_cache(tmp_path):
    svc, fake = _svc(tmp_path)
    v1 = asyncio.run(svc.embed_query("你好"))
    v2 = asyncio.run(svc.embed_query("你好"))
    assert v1 == v2 == [0.1] * 4
    assert fake.query_calls == 1  # 第二次命中缓存，不再调嵌入器


def test_embed_query_different_text_misses(tmp_path):
    svc, fake = _svc(tmp_path)
    asyncio.run(svc.embed_query("你好"))
    asyncio.run(svc.embed_query("再见"))
    assert fake.query_calls == 2


def test_embed_documents_batch_and_partial_hit(tmp_path):
    svc, fake = _svc(tmp_path)
    r1 = asyncio.run(svc.embed_documents(["a", "b"]))  # 全缺失 → 1 次批量调用
    assert fake.doc_calls == 1
    r2 = asyncio.run(svc.embed_documents(["a", "c"]))  # a 命中, c 缺失 → 仍 1 次
    assert fake.doc_calls == 2
    assert r1[0] == r2[0] == [0.1] * 4
    assert len(r2) == 2


def test_embed_documents_empty(tmp_path):
    svc, fake = _svc(tmp_path)
    assert asyncio.run(svc.embed_documents([])) == []
    assert fake.doc_calls == 0


def test_embed_query_no_cache_when_disabled():
    fake = CountingEmbedder()
    config = BotConfig(embed_dimensions=4, embed_cache_enabled=False)
    svc = EmbeddingService(config, embedder=fake, cache=None)
    asyncio.run(svc.embed_query("x"))
    asyncio.run(svc.embed_query("x"))
    assert fake.query_calls == 2  # 禁用缓存 → 每次都调


def test_model_change_invalidates_key(tmp_path):
    svc, fake = _svc(tmp_path)
    text = "Instruct: 检索群聊历史中与问题最相关的消息\nQuery: 你好"
    key_a = svc._cache_key(text)
    svc._config.embed_model = "model-b"
    key_b = svc._cache_key(text)
    assert key_a != key_b  # 换模型 → 新 key 空间，旧缓存自然失效
