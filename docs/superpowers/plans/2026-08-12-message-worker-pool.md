# 消息多 Worker 并发 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `MessageHandler` 从单 worker 改为可配置的多 worker asyncio 池，使不同 `thread_id` 可以并发处理，同时保持同一会话严格串行。

**Architecture:** 共享 `asyncio.Queue` + N 个 worker task + 每 `thread_id` 一把 `asyncio.Lock`。队列容量和 worker 数量由 `BotConfig` 控制；worker 数量为 1 时保持现有行为。并发安全方面为共享的 Milvus/Ollama 底层调用加最小粒度锁，避免多 worker 同时访问非线程安全客户端。

**Tech Stack:** Python 3.12、asyncio、pydantic-settings、aiosqlite、pymilvus；无新增依赖。

## Global Constraints

- 测试与 lint 命令：`uv run pytest`、`uv run ruff check`。
- 现有测试必须全部保持通过。
- `MessageHandler` 新增参数必须带默认值，不能破坏现有构造调用。
- `BOT_MESSAGE_WORKER_COUNT` 合法范围为 1..64，默认 1。
- `BOT_MESSAGE_QUEUE_MAXSIZE` 合法范围为 >=0，默认 0，0 表示无界。
- 同一个 `thread_id` 必须严格按入队顺序串行；不同 `thread_id` 可以并发。
- 不引入 OS 线程 worker；并发单位为 `asyncio.Task`。
- 并发安全审查中不安全的底层访问必须加锁，不能通过退回单 worker 来规避。

---

### Task 1: BotConfig 消息并发配置

**Files:**
- Modify: `common/config.py`
- Modify: `.env-template`
- Modify: `tests/test_config.py`

**Interfaces:**
- Produces: `BotConfig.message_worker_count: int`、`BotConfig.message_queue_maxsize: int`，环境变量为 `BOT_MESSAGE_WORKER_COUNT`、`BOT_MESSAGE_QUEUE_MAXSIZE`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_config.py` 的 `EXPECTED_DEFAULTS` 末尾追加：

```python
    "message_worker_count": 1,
    "message_queue_maxsize": 0,
```

在 `ENV_SAMPLES` 末尾追加：

```python
    "message_worker_count": ("4", 4),
    "message_queue_maxsize": ("512", 512),
```

在文件末尾追加两个校验测试：

```python
def test_invalid_message_worker_count_rejected(monkeypatch):
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("BOT_MESSAGE_WORKER_COUNT", "0")
    with pytest.raises(ValidationError):
        BotConfig(_env_file=None)


def test_invalid_message_queue_maxsize_rejected(monkeypatch):
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("BOT_MESSAGE_QUEUE_MAXSIZE", "-1")
    with pytest.raises(ValidationError):
        BotConfig(_env_file=None)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_config.py -v`

Expected: FAIL，新增字段不存在、`.env-template` 缺别名或校验测试报错。

- [ ] **Step 3: 实现配置字段**

在 `common/config.py` 的 Transport 段之后追加：

```python
    # --- Message concurrency ---
    message_worker_count: int = Field(
        default=1,
        ge=1,
        le=64,
        validation_alias="BOT_MESSAGE_WORKER_COUNT",
    )
    message_queue_maxsize: int = Field(
        default=0,
        ge=0,
        validation_alias="BOT_MESSAGE_QUEUE_MAXSIZE",
    )
```

在 `.env-template` 的 Transport 段之后追加：

```text
# --- Message concurrency ---
# BOT_MESSAGE_WORKER_COUNT = 1   # asyncio worker 数量；>1 时不同 thread_id 可并发处理，同一会话仍串行
# BOT_MESSAGE_QUEUE_MAXSIZE = 0  # 消息队列上限；0=无界，正整数=满时入队阻塞产生背压
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_config.py -v`

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add common/config.py .env-template tests/test_config.py
git commit -m "feat: add message concurrency config"
```

---

### Task 2: MessageHandler worker 池

**Files:**
- Modify: `bot/handler.py`
- Modify: `main.py`
- Create: `tests/test_message_worker_pool.py`

**Interfaces:**
- Consumes: `BotConfig.message_worker_count`、`BotConfig.message_queue_maxsize`。
- Produces: `MessageHandler(client, graph, persona, api_client, bot_config=None, command_registry=None, command_services=None, worker_count=1, queue_maxsize=0)`；`start()` 创建 N 个 worker task；`stop()` 放入 N 个 sentinel 并等待全部 worker 退出。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_message_worker_pool.py`：

```python
"""MessageHandler worker pool: multi-worker, per-thread ordering, backpressure."""

import asyncio

from bot.handler import MessageHandler
from object.satori import Channel, ChannelType, EventBody, Message, User


class _StubApi:
    async def send_message(self, channel_id, content):
        pass


def _make_handler(graph, worker_count=1, queue_maxsize=0):
    return MessageHandler(
        client=object(),
        graph=graph,
        persona="你是{bot_name}",
        api_client=_StubApi(),
        worker_count=worker_count,
        queue_maxsize=queue_maxsize,
    )


def _event(text: str, channel_id: str = "ch1") -> EventBody:
    return EventBody(
        id=hash(text) % 10_000,
        sn=1,
        type="message-created",
        platform="llonebot",
        channel=Channel(id=channel_id, type=ChannelType.DIRECT),
        user=User(id="u1", name="tester"),
        message=Message(id=f"m-{text}", content=text),
    )


class _OrderedGraph:
    def __init__(self):
        self.calls = []

    async def ainvoke(self, state, config):
        await asyncio.sleep(0.01)
        self.calls.append(state["clean_text"])
        return {"reply_text": ""}


class _BlockingGraph:
    def __init__(self):
        self.entered = []
        self.first_entered = asyncio.Event()
        self.release = asyncio.Event()

    async def ainvoke(self, state, config):
        text = state["clean_text"]
        self.entered.append(text)
        if text == "block":
            self.first_entered.set()
            await self.release.wait()
        return {"reply_text": ""}


def test_worker_pool_starts_and_stops():
    async def run():
        graph = _OrderedGraph()
        handler = _make_handler(graph, worker_count=3)
        await handler.start()
        assert len(handler._worker_tasks) == 3
        await handler.stop()
        assert all(task.done() for task in handler._worker_tasks)

    asyncio.run(run())


def test_same_thread_messages_keep_order_with_multiple_workers():
    async def run():
        graph = _OrderedGraph()
        handler = _make_handler(graph, worker_count=3)
        await handler.start()
        await handler.handle(_event("m1", "g1"))
        await handler.handle(_event("m2", "g1"))
        await handler.handle(_event("m3", "g1"))
        await handler.stop()
        assert graph.calls == ["m1", "m2", "m3"]

    asyncio.run(run())


def test_different_threads_can_run_concurrently():
    async def run():
        graph = _BlockingGraph()
        handler = _make_handler(graph, worker_count=2)
        await handler.start()
        await handler.handle(_event("block", "g1"))
        await graph.first_entered.wait()
        await handler.handle(_event("other", "g2"))
        await asyncio.sleep(0.05)
        assert "other" in graph.entered
        graph.release.set()
        await handler.stop()

    asyncio.run(run())


def test_same_thread_second_message_waits_for_lock():
    async def run():
        graph = _BlockingGraph()
        handler = _make_handler(graph, worker_count=2)
        await handler.start()
        await handler.handle(_event("block", "g1"))
        await graph.first_entered.wait()
        await handler.handle(_event("later", "g1"))
        await asyncio.sleep(0.05)
        assert graph.entered == ["block"]
        graph.release.set()
        await handler.stop()
        assert graph.entered == ["block", "later"]

    asyncio.run(run())


def test_queue_maxsize_blocks_ingress_until_worker_drains():
    async def run():
        graph = _BlockingGraph()
        handler = _make_handler(graph, worker_count=1, queue_maxsize=1)
        first = asyncio.create_task(handler.handle(_event("first", "g1")))
        await first
        second = asyncio.create_task(handler.handle(_event("later", "g1")))
        await asyncio.sleep(0.05)
        assert not second.done()
        await handler.start()
        await second
        await handler.stop()

    asyncio.run(run())
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_message_worker_pool.py -v`

Expected: FAIL，`TypeError` 或 `AttributeError`，因为 `MessageHandler` 还没有 `worker_count`/`_worker_tasks`。

- [ ] **Step 3: 实现 worker 池**

修改 `bot/handler.py`：

`__init__` 签名新增两个参数：

```python
    def __init__(
        self,
        client: SatoriClient,
        graph: CompiledGraph,
        persona: str,
        api_client: SatoriApiClient,
        bot_config=None,
        command_registry: CommandRegistry | None = None,
        command_services: CommandServices | None = None,
        worker_count: int = 1,
        queue_maxsize: int = 0,
    ) -> None:
```

把队列和 worker 状态改为：

```python
        self._worker_count = worker_count
        self._queue: asyncio.Queue[dict | None] = asyncio.Queue(maxsize=queue_maxsize)
        self._worker_tasks: list[asyncio.Task[None]] = []
```

替换 `start()` 与 `stop()`：

```python
    async def start(self) -> None:
        """Start the configured number of background message workers."""
        self._worker_tasks = [
            asyncio.create_task(self._worker())
            for _ in range(self._worker_count)
        ]
        logger.info("Message workers started: %d", self._worker_count)

    async def stop(self) -> None:
        """Signal workers to stop and wait for pending messages."""
        for _ in range(self._worker_count):
            await self._queue.put(None)
        if self._worker_tasks:
            await asyncio.gather(*self._worker_tasks, return_exceptions=True)
            self._worker_tasks = []
        logger.info("Message workers stopped")
```

替换 `_worker()`：

```python
    async def _worker(self) -> None:
        """Background worker: dequeue and process messages.

        Per-thread_id locks serialize same-conversation messages to
        prevent LangGraph checkpoint conflicts.
        """
        while True:
            try:
                item = await self._queue.get()
                if item is None:  # Sentinel — shutdown
                    self._queue.task_done()
                    return
                thread_id: str = item["thread_id"]
                lock = self._locks.setdefault(thread_id, asyncio.Lock())
                async with lock:
                    try:
                        await self._process(item)
                    except Exception:
                        logger.exception("Message processing failed for thread %s", thread_id)
                self._queue.task_done()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Message worker loop error")
```

修改 `main.py` 的 `MessageHandler` 构造：

```python
    handler = MessageHandler(
        client, graph, persona, api_client,
        bot_config=config,
        command_registry=command_registry,
        command_services=command_services,
        worker_count=config.message_worker_count,
        queue_maxsize=config.message_queue_maxsize,
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_message_worker_pool.py tests/test_handler.py -v`

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add bot/handler.py main.py tests/test_message_worker_pool.py
git commit -m "feat: add multi-worker message processing pool"
```

---

### Task 3: 共享 RAG 底层并发安全

**Files:**
- Modify: `bot/core/rag/embedder.py`
- Modify: `bot/core/rag/milvus.py`
- Modify: `tests/test_embed_cache.py`
- Modify: `tests/test_milvus_store.py`

**Interfaces:**
- Consumes: `EmbeddingService`、`MilvusStore` 的现有异步接口。
- Produces: `EmbeddingService` 内部对 `_embeddings.embed_query/embed_documents` 串行化；`MilvusStore` 内部对 `_client` 操作串行化；`MilvusStore.prune` 改为 `async def prune(thread_id: str, keep: int) -> None`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_embed_cache.py` 顶部追加 `import threading`：

```python
import threading
```

在 `CountingEmbedder` 之后追加：

```python
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
```

在文件末尾追加：

```python
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
```

在 `tests/test_milvus_store.py` 顶部追加 `import threading`：

```python
import threading
```

在 `FakeEmbedder` 之后追加：

```python
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
```

把 `test_prune_keeps_newest` 中的调用改为：

```python
    asyncio.run(store.prune("g1", 2))
```

在 `tests/test_milvus_store.py` 末尾追加：

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_embed_cache.py::test_embed_query_serializes_underlying_embedder tests/test_milvus_store.py::test_milvus_client_operations_serialized tests/test_milvus_store.py::test_prune_keeps_newest -v`

Expected: FAIL，`max_active > 1` 或 `store.prune` 不是 awaitable。

- [ ] **Step 3: 实现并发安全**

修改 `bot/core/rag/embedder.py`：

在 `__init__` 中 `self._cache = cache` 后追加：

```python
        self._embed_lock = asyncio.Lock()
```

把 `embed_query` 改为：

```python
    async def embed_query(self, query: str) -> list[float]:
        text = self._query_text(query)
        if self._cache is not None:
            key = self._cache_key("query", query)
            cached = await asyncio.to_thread(self._cache.get, key)
            if cached is not None:
                return cached
            async with self._embed_lock:
                vec = await asyncio.to_thread(self._embeddings.embed_query, text)
            await asyncio.to_thread(self._cache.set, key, self._config.embed_model, query, vec)
            return vec
        async with self._embed_lock:
            return await asyncio.to_thread(self._embeddings.embed_query, text)
```

把 `embed_documents` 改为：

```python
    async def embed_documents(self, contents: list[str]) -> list[list[float]]:
        if not contents:
            return []
        texts = [self._document_text(c) for c in contents]
        if self._cache is None:
            async with self._embed_lock:
                return await asyncio.to_thread(self._embeddings.embed_documents, texts)
        keys = [self._cache_key("document", c) for c in contents]
        cached = await asyncio.to_thread(self._cache.mget, keys)
        missing = [(i, t) for i, t in enumerate(texts) if cached[i] is None]
        if missing:
            idxs = [i for i, _ in missing]
            async with self._embed_lock:
                vecs = await asyncio.to_thread(
                    self._embeddings.embed_documents, [t for _, t in missing]
                )
            pairs = [
                (keys[i], self._config.embed_model, contents[i], v)
                for i, v in zip(idxs, vecs)
            ]
            await asyncio.to_thread(self._cache.mset, pairs)
            for i, v in zip(idxs, vecs):
                cached[i] = v
        return cached
```

修改 `bot/core/rag/milvus.py`：

在 `self._embedder = ...` 后追加：

```python
        self._client_lock = asyncio.Lock()
```

把 `add_texts` 改为：

```python
    async def add_texts(self, texts: list[str], metadatas: list[dict]) -> None:
        """嵌入并插入一批记录；插入后按线程淘汰超限。"""
        if not texts:
            return
        vecs = await self._embedder.embed_documents(texts)
        rows = [
            {**meta, "vector": vec, "text": text}
            for text, meta, vec in zip(texts, metadatas, vecs)
        ]
        async with self._client_lock:
            await asyncio.to_thread(self._client.insert, self._collection, rows)
            for tid in {m["thread_id"] for m in metadatas}:
                self._prune_thread(tid, self._config.rag_retention_per_thread)
```

把 `prune` 改为：

```python
    async def prune(self, thread_id: str, keep: int) -> None:
        """公开淘汰接口（add_texts 内部已自动淘汰；此接口供显式调用/测试）。"""
        async with self._client_lock:
            self._prune_thread(thread_id, keep)
```

把 `search_dense` 改为：

```python
    async def search_dense(
        self, query: str, expr: str, thread_id: str | None, k: int,
    ) -> list[dict]:
        """dense 语义检索：query 嵌入后按 vector 字段 ANN。"""
        vec = await self._embedder.embed_query(query)
        async with self._client_lock:
            raw = await asyncio.to_thread(
                self._client.search,
                self._collection, data=[vec], anns_field="vector",
                filter=_build_filter(expr, thread_id), limit=k,
                search_params={"metric_type": "COSINE"},
                output_fields=_OUTPUT_FIELDS,
            )
        return [_dense_hit(h) for h in raw[0]]
```

把 `search_sparse` 改为：

```python
    async def search_sparse(
        self, query: str, expr: str, thread_id: str | None, k: int,
    ) -> list[dict]:
        """sparse 词法检索：query 文本直接进 BM25 函数（jieba 分词）。"""
        async with self._client_lock:
            raw = await asyncio.to_thread(
                self._client.search,
                self._collection, data=[query], anns_field="sparse",
                filter=_build_filter(expr, thread_id), limit=k,
                search_params={"metric_type": "BM25"},
                output_fields=_OUTPUT_FIELDS,
            )
        return [_sparse_hit(h) for h in raw[0]]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_embed_cache.py tests/test_milvus_store.py tests/test_rag_service.py -v`

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add bot/core/rag/embedder.py bot/core/rag/milvus.py tests/test_embed_cache.py tests/test_milvus_store.py
git commit -m "feat: serialize shared rag clients for multi-worker use"
```

---

### Task 4: 更新 AGENTS.md

**Files:**
- Modify: `AGENTS.md`

**Interfaces:**
- No runtime interface. Documentation only.

- [ ] **Step 1: 修改数据流描述**

把 `AGENTS.md` 数据流中的：

```text
WS 事件 → MessageHandler.handle() → 校验+入队 → worker（按 thread_id 锁串行）→ _process
```

改为：

```text
WS 事件 → MessageHandler.handle() → 校验+入队 → worker 池（N 个 asyncio worker，按 thread_id 锁串行）→ _process
```

- [ ] **Step 2: 检查文档变更**

Run: `rg -n "worker|BOT_MESSAGE_WORKER_COUNT|BOT_MESSAGE_QUEUE_MAXSIZE" AGENTS.md .env-template`

Expected: 数据流包含 `worker 池`，`.env-template` 包含两个新环境变量。

- [ ] **Step 3: Commit**

```bash
git add AGENTS.md
git commit -m "docs: document message worker pool"
```

---

### Task 5: 全量验证

**Files:**
- No source changes in this task.

- [ ] **Step 1: 运行 lint**

Run: `uv run ruff check`

Expected: PASS。

- [ ] **Step 2: 运行全量测试**

Run: `uv run pytest`

Expected: PASS。

- [ ] **Step 3: 运行快速配置 sanity check**

Run:

```powershell
uv run python -c "from common import BotConfig; c = BotConfig(_env_file=None); print(c.message_worker_count, c.message_queue_maxsize)"
```

Expected: `1 0`。
