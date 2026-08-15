"""EmbeddingCache + EmbeddingService 嵌入缓存：命中跳过嵌入 API、批量、禁用、淘汰。"""

import asyncio
import threading

from langchain_openai import OpenAIEmbeddings

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


class BlockingEmbedder(CountingEmbedder):
    def __init__(self):
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()
        self.active = 0
        self.max_active = 0

    def embed_query(self, text):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.entered.set()
        self.release.wait(2)
        self.active -= 1
        return [0.1] * 4


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


def test_cache_concurrent_reads_and_writes_are_serialized(tmp_path):
    cache = EmbeddingCache(db_path=str(tmp_path / "c.sqlite"))
    errors = []

    async def writer(i: int) -> None:
        try:
            await asyncio.to_thread(
                cache.set, f"k{i % 10}", "m", f"text{i}", [float(i)]
            )
        except Exception as exc:
            errors.append((type(exc).__name__, str(exc)))

    async def reader() -> None:
        try:
            await asyncio.to_thread(cache.get, "k0")
            await asyncio.to_thread(cache.mget, ["k0", "k1", "k2"])
        except Exception as exc:
            errors.append((type(exc).__name__, str(exc)))

    async def run() -> None:
        await asyncio.gather(
            *[writer(i) for i in range(50)],
            *[reader() for _ in range(50)],
        )

    asyncio.run(run())
    assert errors == []
    assert cache.get("k9") is not None
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
    svc, _ = _svc(tmp_path)
    key_a = svc._cache_key("query", "你好")
    svc._config.embed_model = "model-b"
    key_b = svc._cache_key("query", "你好")
    assert key_a != key_b  # 换模型 → 新 key 空间，旧缓存自然失效


def test_query_document_same_content_no_cross_hit(tmp_path):
    """同内容 Query/Document 前缀不同 → 向量不同，key 必须分开，互不串用。"""
    svc, fake = _svc(tmp_path)
    asyncio.run(svc.embed_documents(["你好"]))
    asyncio.run(svc.embed_query("你好"))
    assert fake.doc_calls == 1
    assert fake.query_calls == 1  # query 未命中 document 的缓存


def test_cache_text_stores_raw_content(tmp_path):
    """text 列只存原始内容，不带 Instruct 前缀。"""
    svc, _ = _svc(tmp_path)
    asyncio.run(svc.embed_query("你好"))
    asyncio.run(svc.embed_documents(["再见"]))
    cache = svc._cache
    for raw in ("你好", "再见"):
        rows = cache.conn.execute(
            "SELECT text FROM embed_cache WHERE text = ?", (raw,)
        ).fetchall()
        assert rows and rows[0][0] == raw


def test_embed_query_serializes_underlying_embedder():
    fake = BlockingEmbedder()
    config = BotConfig(embed_dimensions=4, embed_cache_enabled=False)
    svc = EmbeddingService(config, embedder=fake, cache=None)

    async def run():
        first = asyncio.create_task(svc.embed_query("a"))
        await asyncio.to_thread(fake.entered.wait, 2)
        second = asyncio.create_task(svc.embed_query("b"))
        await asyncio.sleep(0.05)
        assert fake.max_active == 1
        fake.release.set()
        await asyncio.gather(first, second)

    asyncio.run(run())


def test_embedding_service_builds_openai_compatible_client():
    config = BotConfig(
        _env_file=None,
        embed_model="embed-model",
        embed_base_url="https://embed.example",
        embed_api_key="sk-embed",
        embed_dimensions=4,
        embed_cache_enabled=False,
    )
    svc = EmbeddingService(config, cache=None)
    assert isinstance(svc._embeddings, OpenAIEmbeddings)
    assert svc._embeddings.model == "embed-model"
    assert svc._embeddings.openai_api_base == "https://embed.example/v1"
    assert svc._embeddings.dimensions == 4
    assert svc._embeddings.check_embedding_ctx_length is False


def test_embedding_service_keeps_v1_base_url():
    config = BotConfig(
        _env_file=None,
        embed_base_url="https://embed.example/v1/",
        embed_api_key="sk-embed",
        embed_dimensions=4,
        embed_cache_enabled=False,
    )
    svc = EmbeddingService(config, cache=None)
    assert svc._embeddings.openai_api_base == "https://embed.example/v1"
