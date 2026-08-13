# RAG 迁移 milvus-lite（dense + sparse 混合检索）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 RAG 存储层从 sqlite-vec 迁移到 milvus-lite（raw pymilvus 直连），用 dense（语义）+ sparse（BM25/jieba）双信号 + 自研 RRF 融合提升群聊历史检索召回。

**Architecture:** `MilvusStore`（raw pymilvus：建集合、insert、字段级 dense/sparse 检索、淘汰）替代 `RagVectorStore`；`RagService` 重写为 `hybrid_search` 编排层（dense 候选按阈值过滤、sparse 无阈值、当前群优先跨群补齐、RRF k=60 融合）。`EmbeddingService` 与 `EmbeddingCache` **不变**。工具层 `search_chat_history` 接口不变，图/处理器/记忆全链路不受影响。

**Tech Stack:** milvus-lite 3.2.0（嵌入式 Milvus）+ raw pymilvus 3.0.1、jieba（milvus 内置 BM25 分析器）、Ollama qwen3-embedding（dense）、自研 RRF。

## Global Constraints

- 存储后端：milvus-lite，**raw pymilvus 直连**（`MilvusClient`），弃用 langchain-milvus 高层封装（D0）。
- 混合检索：**自研 RRF**，常数 `k=60`：`score(doc) = Σ 1/(60 + rank_i)`（D1）。
- `content_keyword` 映射：sparse BM25 检索（D2）；`person` 映射：expr 里的前缀匹配 `like '{person}%'`（**行为变化**：原 SQL 子串）。
- 旧 `db/rag.sqlite`：**直接删除**，不迁移不保留（D3）。
- 中文分词：milvus 内置 jieba 分析器（配在 `text` 字段上，**不是** Function params）（D4）。
- 检索 API：`search_dense` / `search_sparse` / 自研 `hybrid_search` 3 种；**不用 `client.query` 作检索**（person/content 过滤并入 expr）（D5）。
- 连接：`MilvusClient(uri="./milvus.db")`（bot 从项目根运行时落 `F:\PythonProject\qq-bot\milvus.db`）（D6）。
- 文档知识库 = 未来扩展（届时加 `kind`/`doc_id`/`doc_title`/`chunk_index` 字段）；本期仅聊天 3 种检索，schema 不含 `kind`（D7）。
- 线程隔离：expr `thread_id == 'X'`（partition key 自动路由），**不是** `partition_names`。
- 三个 schema 踩坑（已实测）：① BM25 函数输出字段 `sparse` 必须先在 schema 声明；② analyzer 配在 `text` 字段；③ 动态字段 expr 可过滤。
- 全部存储操作 try/except 降级：写失败跳过索引、搜失败返回空，**绝不崩图**。
- `BOT_RAG_*` 配置语义不变：`BOT_RAG_TOP_K`（最终 top-N）、`BOT_RAG_SCORE_THRESHOLD`（dense 候选阈值）、`BOT_RAG_RETENTION_PER_THREAD`（淘汰）、`BOT_EMBED_*`（嵌入不变）。
- `CANDIDATE_K = 50`（对齐旧 `candidate_k=50`）。

---

### Task 1: 依赖增删 + uv sync

把 pyproject 从 sqlite-vec 切换到 milvus-lite + pymilvus + jieba，并删掉已弃用的 pkuseg 构建残留。

**Files:**
- Modify: `pyproject.toml`
- Test: 无新测试文件（命令行冒烟验证）

**Interfaces:**
- Consumes: 无
- Produces: 环境中可 `import pymilvus / milvus_lite / jieba`；后续任务直接使用

- [ ] **Step 1: 改 `pyproject.toml` 依赖**

把 `dependencies` 里的 `"sqlite-vec>=0.1.9",` 整行删除，换成：

```toml
    "jieba>=0.42.1",
    "milvus-lite>=3.2.0",
    "pymilvus>=3.0.1",
```

同时删除整个 `[tool.uv.extra-build-dependencies]` 段（pkuseg 已弃用，残留无效）：

```toml
[tool.uv.extra-build-dependencies]
pkuseg = ["numpy"]
```

- [ ] **Step 2: `uv sync` 安装**

Run: `uv sync`
Expected: 成功解析安装 milvus-lite / pymilvus / jieba，无编译报错（aliyun 镜像，Python 3.12）。

- [ ] **Step 3: 冒烟验证可导入**

Run:
```bash
uv run python -c "import pymilvus, milvus_lite, jieba; print('pymilvus', pymilvus.__version__); print('ok')"
```
Expected: 打印 `pymilvus <版本号>` 与 `ok`（milvus-lite 无顶层 `__version__` 属正常）。

- [ ] **Step 4: 确认 sqlite-vec 已移除**

Run:
```bash
uv run python -c "import importlib.util; print('sqlite_vec present:', importlib.util.find_spec('sqlite_vec') is not None)"
```
Expected: 打印 `sqlite_vec present: False`。

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build: RAG 迁移 milvus-lite，删 sqlite-vec/pkuseg，加 pymilvus/milvus-lite/jieba"
```

---

### Task 2: RRF 融合纯函数

新增独立的 RRF 模块（`bot/core/rag/rrf.py`），供 `RagService.hybrid_search` 合并 dense + sparse 候选。纯函数、无 I/O，TDD 先行。

**Files:**
- Create: `bot/core/rag/rrf.py`
- Test: `tests/test_rrf.py`

**Interfaces:**
- Consumes: 无
- Produces: `rrf_merge(ranked_lists: list[list[dict]], k: int = 60) -> list[dict]`——每个候选 dict 含 `"id"` 主键；跨列表重复 id 分数叠加、只返回一次；按分数降序、同分按首次 rank 升序。

- [ ] **Step 1: 写失败测试 `tests/test_rrf.py`**

```python
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
```

- [ ] **Step 2: 运行验证失败**

Run: `uv run pytest tests/test_rrf.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'bot.core.rag.rrf'`）

- [ ] **Step 3: 实现 `bot/core/rag/rrf.py`**

```python
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
```

- [ ] **Step 4: 运行验证通过**

Run: `uv run pytest tests/test_rrf.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_rrf.py bot/core/rag/rrf.py
git commit -m "feat: RRF 融合纯函数（k=60，跨信号叠加 + 去重）"
```

---

### Task 3: MilvusStore（raw pymilvus 存储层）

实现 `bot/core/rag/milvus.py`：建集合（dense + sparse 双字段、BM25 函数 + jieba、thread_id partition key、双索引）、`add_texts`（嵌入 + insert + 淘汰）、`search_dense` / `search_sparse`（字段级检索）、`prune`、`close`。测试用 tmp uri 真实 milvus-lite + FakeEmbedder（不连 Ollama）。

**Files:**
- Create: `bot/core/rag/milvus.py`
- Test: `tests/test_milvus_store.py`（替代 `tests/test_rag_store.py`，`test_rag_store.py` 本任务后删除）

**Interfaces:**
- Consumes: `config.embed_dimensions`、`config.rag_retention_per_thread`（BotConfig）；`EmbeddingService.embed_query / embed_documents`（`bot/core/rag/embedder.py`，**不变**）
- Produces:
  - `MilvusStore(config, uri="./milvus.db", collection="chat", embedder=None)`
  - `async add_texts(texts: list[str], metadatas: list[dict]) -> None`（插入后按 thread 淘汰超限）
  - `async search_dense(query: str, expr: str, thread_id: str | None, k: int) -> list[dict]`（score = `1 - cosine_distance`）
  - `async search_sparse(query: str, expr: str, thread_id: str | None, k: int) -> list[dict]`（score = BM25 分值）
  - `prune(thread_id: str, keep: int) -> None`、`close() -> None`
  - 命中 dict 字段：`id`（pk，供 RRF 去重）、`thread_id`、`sender_id`、`sender_name`、`receiver_id`、`receiver_name`、`content`、`timestamp`、`score`

- [ ] **Step 1: 写失败测试 `tests/test_milvus_store.py`**

```python
"""MilvusStore 集成测试（真实 milvus-lite，tmp_path 临时库）。

覆盖：add_texts 写库、search_dense / search_sparse、线程隔离（expr）、
expr 过滤（人名 / 时间窗）、prune 淘汰、dense score 语义。
测试注入 FakeEmbedder（确定性向量），不连 Ollama。
"""

import pytest

from bot.core.rag.milvus import MilvusStore
from common import BotConfig


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
    store.add_texts(
        ["关于嵌入模型的讨论", "另一话题"],
        [_meta("g1", "张三", "关于嵌入模型的讨论", "2026-08-01 10:00:00"),
         _meta("g1", "李四", "另一话题", "2026-08-01 11:00:00")],
    )
    hits = store.search_dense("嵌入", "", "g1", k=5)
    assert hits[0]["content"] == "关于嵌入模型的讨论"
    assert hits[0]["score"] > 0.8  # 1 - cosine_distance([1,0,0,0],[0.9,0.1,0,0]) ≈ 0.99
    assert hits[0]["sender_name"] == "张三"


def test_dense_thread_isolation_by_expr(tmp_path):
    store = _store(tmp_path)
    store.add_texts(
        ["关于嵌入模型的讨论", "关于嵌入模型的讨论"],
        [_meta("g1", "张三", "关于嵌入模型的讨论", "2026-08-01 10:00:00"),
         _meta("g2", "王五", "关于嵌入模型的讨论", "2026-08-02 12:00:00")],
    )
    hits = store.search_dense("嵌入", "", "g1", k=5)
    assert {h["thread_id"] for h in hits} == {"g1"}


def test_sparse_search_by_chinese_keyword(tmp_path):
    store = _store(tmp_path)
    store.add_texts(
        ["张三说今晚去吃火锅", "李四在讨论别的事"],
        [_meta("g1", "张三", "张三说今晚去吃火锅", "2026-08-01 10:00:00"),
         _meta("g1", "李四", "李四在讨论别的事", "2026-08-01 11:00:00")],
    )
    hits = store.search_sparse("火锅", "", "g1", k=5)
    assert hits and "火锅" in hits[0]["content"]


def test_sparse_search_no_match_returns_empty(tmp_path):
    store = _store(tmp_path)
    store.add_texts(
        ["张三说今晚去吃火锅"],
        [_meta("g1", "张三", "张三说今晚去吃火锅", "2026-08-01 10:00:00")],
    )
    hits = store.search_sparse("量子计算", "", "g1", k=5)
    assert hits == []


def test_expr_person_filter(tmp_path):
    store = _store(tmp_path)
    store.add_texts(
        ["张三说今晚去吃火锅", "李四说今晚去吃火锅"],
        [_meta("g1", "张三", "张三说今晚去吃火锅", "2026-08-01 10:00:00"),
         _meta("g1", "李四", "李四说今晚去吃火锅", "2026-08-01 11:00:00")],
    )
    # 跨群（thread_id=None）+ 人名前缀过滤
    hits = store.search_sparse("火锅", "sender_name like '张%'", None, k=5)
    assert len(hits) == 1
    assert hits[0]["sender_name"] == "张三"


def test_expr_time_window(tmp_path):
    store = _store(tmp_path)
    store.add_texts(
        ["旧消息关于嵌入模型", "新消息关于嵌入模型"],
        [_meta("g1", "张三", "旧消息关于嵌入模型", "2026-07-01 10:00:00"),
         _meta("g1", "张三", "新消息关于嵌入模型", "2026-08-01 10:00:00")],
    )
    hits = store.search_dense(
        "嵌入", "timestamp >= '2026-08-01 00:00:00'", "g1", k=5,
    )
    assert len(hits) == 1
    assert hits[0]["content"] == "新消息关于嵌入模型"


def test_add_texts_enforces_retention(tmp_path):
    store = _store(tmp_path, retention=2)
    store.add_texts(
        ["m1", "m2", "m3"],
        [_meta("g1", "张三", "m1", "2026-08-01 10:00:00"),
         _meta("g1", "张三", "m2", "2026-08-01 11:00:00"),
         _meta("g1", "张三", "m3", "2026-08-01 12:00:00")],
    )
    hits = store.search_dense("m", "", "g1", k=10)
    # 3 条按 timestamp DESC 淘汰到 2 条（保留最新）
    assert {h["content"] for h in hits} == {"m2", "m3"}


def test_prune_keeps_newest(tmp_path):
    store = _store(tmp_path, retention=100)
    store.add_texts(
        ["m1", "m2", "m3"],
        [_meta("g1", "张三", "m1", "2026-08-01 10:00:00"),
         _meta("g1", "张三", "m2", "2026-08-01 11:00:00"),
         _meta("g1", "张三", "m3", "2026-08-01 12:00:00")],
    )
    store.prune("g1", 2)
    hits = store.search_dense("m", "", "g1", k=10)
    assert {h["content"] for h in hits} == {"m2", "m3"}
```

- [ ] **Step 2: 运行验证失败**

Run: `uv run pytest tests/test_milvus_store.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'bot.core.rag.milvus'`）

- [ ] **Step 3: 实现 `bot/core/rag/milvus.py`**

```python
"""MilvusStore — milvus-lite 向量存储与混合检索（raw pymilvus 直连）。

dense（语义）+ sparse（BM25）双字段集合，thread_id 为 partition key：
- add_texts: 文本 → EmbeddingService.embed_documents（dense）+ client.insert
  （text 字段经 BM25 内置函数自动生成 sparse）。
- search_dense / search_sparse: 字段级检索，expr + thread_id 组装过滤表达式。
- prune: 每线程超限按 timestamp DESC 淘汰最旧。
全部操作由调用方（RagService）try/except 包裹降级，失败不崩图。
"""

import asyncio
import logging

from pymilvus import DataType, Function, FunctionType, MilvusClient

from bot.core.rag.embedder import EmbeddingService

logger = logging.getLogger(__name__)

# pymilvus 单次 query 返回上限（硬限制）
_QUERY_LIMIT = 16384

# search 返回的元数据字段（动态字段 + thread_id 实字段）
_OUTPUT_FIELDS = [
    "thread_id", "sender_id", "sender_name",
    "receiver_id", "receiver_name", "content", "timestamp",
]


def _esc(value: str) -> str:
    """转义 Milvus 表达式中的字符串值（反斜杠 + 单引号）。"""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _build_filter(expr: str, thread_id: str | None) -> str:
    """把语义过滤表达式与线程隔离合并成 Milvus filter 字符串。

    expr 非空且 thread_id 给定 → 两者 ``&&``（partition key 自动路由优化）；
    仅 thread_id → 仅线程过滤；仅 expr（或两者都空）→ 原样（跨群检索）。
    """
    if expr and thread_id:
        return f"{expr} && thread_id == '{_esc(thread_id)}'"
    if thread_id:
        return f"thread_id == '{_esc(thread_id)}'"
    return expr


def _dense_hit(hit: dict) -> dict:
    """dense 命中：score = 1 - cosine_distance（越大越相关）。"""
    entity = hit.get("entity") or {}
    return {**entity, "id": hit["id"], "score": 1.0 - hit["distance"]}


def _sparse_hit(hit: dict) -> dict:
    """sparse 命中：score = BM25 分值（越大越相关，无阈值）。"""
    entity = hit.get("entity") or {}
    return {**entity, "id": hit["id"], "score": hit["distance"]}


class MilvusStore:
    """milvus-lite 存储：建集合、插入、字段级混合检索、淘汰。"""

    def __init__(
        self,
        config,
        uri: str = "./milvus.db",
        collection: str = "chat",
        embedder=None,
    ) -> None:
        self._config = config
        self._collection = collection
        self._embedder = embedder or EmbeddingService(config)
        self._client = MilvusClient(uri=uri)
        self._ensure_collection()
        logger.info("MilvusStore ready (uri=%s, collection=%s)", uri, collection)

    # ------------------------------------------------------------------
    # 集合创建
    # ------------------------------------------------------------------

    def _ensure_collection(self) -> None:
        """集合不存在才建：双向量字段 + BM25 函数 + thread_id partition key。"""
        if self._client.has_collection(self._collection):
            return
        schema = self._client.create_schema(auto_id=True, enable_dynamic_field=True)
        schema.add_field("pk", DataType.INT64, is_primary=True)
        schema.add_field("vector", DataType.FLOAT_VECTOR, dim=self._config.embed_dimensions)
        schema.add_field("sparse", DataType.SPARSE_FLOAT_VECTOR)  # 踩坑1：BM25 输出字段先声明
        schema.add_field(  # 踩坑2：analyzer 在 text 字段上，不在 Function
            "text", DataType.VARCHAR, max_length=65535,
            enable_analyzer=True, analyzer_params={"tokenizer": "jieba"},
        )
        schema.add_field(
            "thread_id", DataType.VARCHAR, max_length=128, is_partition_key=True,
        )
        schema.add_function(
            Function(
                name="bm25_fn", function_type=FunctionType.BM25,
                input_field_names=["text"], output_field_names=["sparse"],
            )
        )
        index_params = self._client.prepare_index_params()
        index_params.add_index(
            "vector", index_type="HNSW", metric_type="COSINE",
            params={"M": 8, "efConstruction": 64},
        )
        index_params.add_index(
            "sparse", index_type="SPARSE_INVERTED_INDEX", metric_type="BM25",
        )
        self._client.create_collection(self._collection, schema=schema, index_params=index_params)
        logger.info("Created milvus collection '%s'", self._collection)

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------

    async def add_texts(self, texts: list[str], metadatas: list[dict]) -> None:
        """嵌入并插入一批记录；插入后按线程淘汰超限。"""
        if not texts:
            return
        vecs = await self._embedder.embed_documents(texts)
        rows = [
            {**meta, "vector": vec, "text": text}
            for text, meta, vec in zip(texts, metadatas, vecs)
        ]
        await asyncio.to_thread(self._client.insert, self._collection, rows)
        for tid in {m["thread_id"] for m in metadatas}:
            self._prune_thread(tid, self._config.rag_retention_per_thread)

    def _prune_thread(self, thread_id: str, keep: int) -> None:
        """删除每线程超出 keep 的最旧记录（timestamp DESC）。"""
        if keep <= 0:
            return
        rows = self._client.query(
            self._collection,
            filter=f"thread_id == '{_esc(thread_id)}'",
            output_fields=["pk", "timestamp"],
            order_by="timestamp DESC",
            limit=_QUERY_LIMIT,
        )
        if len(rows) <= keep:
            return
        ids = [r["pk"] for r in rows[keep:]]
        self._client.delete(self._collection, filter=f"pk in [{', '.join(map(str, ids))}]")

    def prune(self, thread_id: str, keep: int) -> None:
        """公开淘汰接口（add_texts 内部已自动淘汰；此接口供显式调用/测试）。"""
        self._prune_thread(thread_id, keep)

    # ------------------------------------------------------------------
    # 检索（字段级，D5：不用 client.query 作检索）
    # ------------------------------------------------------------------

    async def search_dense(
        self, query: str, expr: str, thread_id: str | None, k: int,
    ) -> list[dict]:
        """dense 语义检索：query 嵌入后按 vector 字段 ANN。"""
        vec = await self._embedder.embed_query(query)
        raw = await asyncio.to_thread(
            self._client.search,
            self._collection, data=[vec], anns_field="vector",
            filter=_build_filter(expr, thread_id), limit=k,
            search_params={"metric_type": "COSINE"},
            output_fields=_OUTPUT_FIELDS,
        )
        return [_dense_hit(h) for h in raw[0]]

    async def search_sparse(
        self, query: str, expr: str, thread_id: str | None, k: int,
    ) -> list[dict]:
        """sparse 词法检索：query 文本直接进 BM25 函数（jieba 分词）。"""
        raw = await asyncio.to_thread(
            self._client.search,
            self._collection, data=[query], anns_field="sparse",
            filter=_build_filter(expr, thread_id), limit=k,
            search_params={"metric_type": "BM25"},
            output_fields=_OUTPUT_FIELDS,
        )
        return [_sparse_hit(h) for h in raw[0]]

    def close(self) -> None:
        self._client.close()
        self._embedder.close()
```

- [ ] **Step 4: 运行验证通过**

Run: `uv run pytest tests/test_milvus_store.py -v`
Expected: 全部 PASS（首次跑 milvus-lite 建库/建索引需几秒，属正常）。

如果 `search_dense` 因索引未就绪报错，在测试临时目录重试一次即可——milvus-lite 对少量数据会自动回退暴力检索；若仍失败，检查 `search_params` 的 `metric_type` 与索引一致。

- [ ] **Step 5: 删除旧 store 测试**

删除 `tests/test_rag_store.py`（被 `test_milvus_store.py` 完全替代）：

```bash
git rm tests/test_rag_store.py
```

- [ ] **Step 6: Commit**

```bash
git add tests/test_milvus_store.py bot/core/rag/milvus.py
git commit -m "feat: MilvusStore 存储层（dense+sparse 字段级检索、BM25+jieba、partition key、淘汰）"
```

---

### Task 4: RagService 重写（hybrid_search 编排）

重写 `bot/core/rag/service.py`：`index_turn` 把文本 + 元数据交给 `MilvusStore.add_texts`；`search` / `search_by_user` 统一委托自研 `hybrid_search`（dense 阈值过滤 + sparse + 当前群优先跨群补齐 + RRF 融合）。同步适配 `tests/test_rag_service.py`（用 FakeMilvusStore，不再需要 FakeEmbedder）与 `tests/fakes.py` 的 `StubRagService.search` 签名。

**Files:**
- Modify: `bot/core/rag/service.py`（重写）
- Modify: `tests/test_rag_service.py`（适配）
- Modify: `tests/fakes.py`（StubRagService.search 去掉 score_threshold 参数）
- Modify: `bot/core/tools/search_chat_history.py:54-59`（docstring：SQL 属性检索 → BM25 稀疏信号）

**Interfaces:**
- Consumes: `MilvusStore`（Task 3）、`rrf_merge`（Task 2）、`config.rag_top_k` / `config.rag_score_threshold`
- Produces:
  - `normalize_time(text: str) -> str`（不变）
  - `_build_expr(person, start_time, end_time) -> str`（纯函数）
  - `RagService(config, store=None)`；`enabled` 属性不变
  - `async index_turn(thread_id, user_id, user_name, bot_id, bot_name, user_message, bot_reply)`（签名不变）
  - `async search(query, thread_id, top_k=None, hours=0, start_time="", end_time="")`（**去掉 score_threshold**）
  - `async search_by_user(thread_id=None, person="", content_keyword="", hours=0, start_time="", end_time="", limit=10)`（签名不变）
  - `async hybrid_search(query, thread_id, person="", content_keyword="", hours=0, start_time="", end_time="", top_k=None)`（新）
  - `close()`

- [ ] **Step 1: 适配 `tests/fakes.py` StubRagService.search 签名**

把 `tests/fakes.py` 里：

```python
    async def search(self, query, thread_id, top_k=None, score_threshold=None,
                     hours=0, start_time="", end_time=""):
```

改为：

```python
    async def search(self, query, thread_id, top_k=None, hours=0, start_time="", end_time=""):
```

（方法体不变；`search_by_user` 不变。）

- [ ] **Step 2: 写失败测试（重写 `tests/test_rag_service.py`）**

```python
"""RagService 编排测试：index_turn 配对、hybrid_search 委托 / 阈值 / 跨群补齐。

注入 FakeMilvusStore（记录调用），不碰真实 milvus / Ollama。
"""

import asyncio
from datetime import datetime

from bot.core.rag.service import RagService, TS_FMT
from common import BotConfig


class FakeMilvusStore:
    """记录 add_texts / search_dense / search_sparse 调用的假存储。

    scripted: 可注入脚本化命中，供阈值过滤 / 跨群补齐测试。
    """

    def __init__(self, dense_hits=None, sparse_hits=None) -> None:
        self.added: list[tuple[list[str], list[dict]]] = []
        self.dense_calls: list[dict] = []
        self.sparse_calls: list[dict] = []
        self.dense_hits = dense_hits or []
        self.sparse_hits = sparse_hits or []

    async def add_texts(self, texts: list[str], metadatas: list[dict]) -> None:
        self.added.append((texts, metadatas))

    async def search_dense(self, query, expr, thread_id, k):
        self.dense_calls.append({"query": query, "expr": expr, "thread_id": thread_id, "k": k})
        return list(self.dense_hits)

    async def search_sparse(self, query, expr, thread_id, k):
        self.sparse_calls.append({"query": query, "expr": expr, "thread_id": thread_id, "k": k})
        return list(self.sparse_hits)

    def close(self) -> None:
        pass


def _svc(store: FakeMilvusStore) -> RagService:
    return RagService(
        BotConfig(rag_enabled=True, rag_top_k=5, rag_score_threshold=0.5),
        store=store,
    )


def _hit(doc_id, content, thread_id="g1", score=0.9, sender="张三"):
    return {
        "id": doc_id, "thread_id": thread_id, "sender_id": "u1", "sender_name": sender,
        "receiver_id": "", "receiver_name": "", "content": content,
        "timestamp": "2026-08-01 10:00:00", "score": score,
    }


# --- index_turn：配对过滤（与旧语义一致） ----------------------------------

def test_index_turn_with_reply_indexes_two():
    store = FakeMilvusStore()
    svc = _svc(store)
    asyncio.run(svc.index_turn("t", "u1", "张三", "bot1", "小助手", "晚上吃什么", "去吃火锅"))

    assert len(store.added) == 1
    texts, metas = store.added[0]
    assert texts == ["晚上吃什么", "去吃火锅"]
    assert metas[0]["sender_id"] == "u1"
    assert metas[0]["sender_name"] == "张三"
    assert metas[0]["receiver_id"] == "bot1"
    assert metas[0]["receiver_name"] == "小助手"
    assert metas[1]["sender_id"] == "bot1"
    assert metas[1]["sender_name"] == "小助手"
    assert metas[1]["receiver_id"] == "u1"
    assert metas[1]["receiver_name"] == "张三"


def test_index_turn_without_reply_indexes_only_user():
    store = FakeMilvusStore()
    svc = _svc(store)
    asyncio.run(svc.index_turn("t", "u1", "张三", "bot1", "小助手", "晚上吃什么", ""))

    texts, metas = store.added[0]
    assert texts == ["晚上吃什么"]
    assert metas[0]["receiver_name"] == ""  # 非回复轮（群广播），无特定接收者


def test_index_turn_all_empty_is_noop():
    store = FakeMilvusStore()
    svc = _svc(store)
    asyncio.run(svc.index_turn("t", "u1", "张三", "bot1", "小助手", "", ""))
    assert store.added == []


def test_index_turn_disabled_is_noop():
    store = FakeMilvusStore()
    svc = RagService(BotConfig(rag_enabled=False), store=store)
    asyncio.run(svc.index_turn("t", "u1", "张三", "bot1", "小助手", "你好", "在的"))
    assert store.added == []


# --- hybrid_search：委托 / 阈值 / 补齐 -------------------------------------

def test_search_calls_dense_and_sparse():
    # 5 条本群命中（≥ top_k=5）→ 不触发跨群补齐，恰好各 1 次 dense/sparse
    store = FakeMilvusStore(
        dense_hits=[_hit(i, f"命中{i}") for i in range(5)],
    )
    svc = _svc(store)
    hits = asyncio.run(svc.search("嵌入模型", "t"))
    assert len(store.dense_calls) == 1
    assert store.dense_calls[0]["query"] == "嵌入模型"
    assert store.dense_calls[0]["thread_id"] == "t"
    assert store.dense_calls[0]["k"] == 50  # CANDIDATE_K
    assert store.sparse_calls[0]["query"] == "嵌入模型"  # query 兼作 sparse 信号
    assert hits[0]["content"] == "命中0"


def test_search_dense_below_threshold_filtered():
    store = FakeMilvusStore(dense_hits=[_hit(1, "低分命中", score=0.2)])
    svc = _svc(store)
    hits = asyncio.run(svc.search("嵌入模型", "t"))
    assert hits == []  # score 0.2 < rag_score_threshold 0.5 → 被滤掉


def test_search_cross_guild_fill_when_insufficient():
    store = FakeMilvusStore(dense_hits=[], sparse_hits=[])
    svc = _svc(store)
    asyncio.run(svc.search("嵌入模型", "t"))
    # 本群 0 命中 < top_k → 触发跨群补齐（thread_id=None）
    assert len(store.dense_calls) == 2
    assert store.dense_calls[1]["thread_id"] is None
    assert store.sparse_calls[1]["thread_id"] is None


def test_search_no_fill_when_in_thread_already_enough():
    store = FakeMilvusStore(
        dense_hits=[_hit(i, f"命中{i}", score=0.9) for i in range(5)],
    )
    svc = _svc(store)
    hits = asyncio.run(svc.search("嵌入模型", "t"))
    assert len(store.dense_calls) == 1  # 5 条 ≥ top_k=5 → 不跨群
    assert len(hits) == 5


def test_search_by_user_person_only_returns_empty():
    store = FakeMilvusStore()
    svc = _svc(store)
    hits = asyncio.run(svc.search_by_user(None, person="张三"))
    assert hits == []  # person-only：无 content/query → 跳过双信号
    assert store.dense_calls == []
    assert store.sparse_calls == []


def test_search_by_user_content_keyword_sparse_only():
    store = FakeMilvusStore(sparse_hits=[_hit(1, "谁说过qwen3")])
    svc = _svc(store)
    hits = asyncio.run(svc.search_by_user(None, content_keyword="qwen3"))
    assert store.dense_calls == []  # query 空 → 跳过 dense
    assert store.sparse_calls[0]["query"] == "qwen3"
    assert store.sparse_calls[0]["thread_id"] is None  # 跨全部群
    assert hits[0]["content"] == "谁说过qwen3"


def test_search_by_user_person_enters_expr():
    store = FakeMilvusStore(sparse_hits=[_hit(1, "张三说今晚吃火锅")])
    svc = _svc(store)
    asyncio.run(svc.search_by_user(None, person="张三", content_keyword="火锅"))
    expr = store.sparse_calls[0]["expr"]
    assert "sender_name like '张三%'" in expr
    assert "receiver_name like '张三%'" in expr


def test_search_hours_sets_iso_since_in_expr():
    store = FakeMilvusStore()
    svc = _svc(store)
    asyncio.run(svc.search("嵌入模型", "t", hours=24))
    expr = store.dense_calls[0]["expr"]
    assert expr.startswith("timestamp >= '")
    since = expr.split("'")[1]
    elapsed = (datetime.now() - datetime.strptime(since, TS_FMT)).total_seconds()
    assert 23 * 3600 < elapsed < 25 * 3600  # ≈ 24h 前


def test_search_by_user_explicit_window_wins_over_hours():
    store = FakeMilvusStore(sparse_hits=[_hit(1, "x")])
    svc = _svc(store)
    asyncio.run(svc.search_by_user(
        None, content_keyword="x",
        start_time="2026-07-01 00:00:00", end_time="2026-08-01 23:59:59", hours=24,
    ))
    expr = store.sparse_calls[0]["expr"]
    assert "timestamp >= '2026-07-01 00:00:00'" in expr
    assert "timestamp <= '2026-08-01 23:59:59'" in expr


def test_search_failure_returns_empty():
    class Boom(FakeMilvusStore):
        async def search_dense(self, query, expr, thread_id, k):
            raise RuntimeError("milvus down")

    svc = _svc(Boom())
    hits = asyncio.run(svc.search("嵌入模型", "t"))
    assert hits == []
```

- [ ] **Step 3: 运行验证失败**

Run: `uv run pytest tests/test_rag_service.py -v`
Expected: 大部分 FAIL——新 `RagService` 构造函数签名（`store` 关键词）与 `hybrid_search` 尚不存在，FakeMilvusStore 无法注入。

- [ ] **Step 4: 重写 `bot/core/rag/service.py`**

```python
"""RagService — 组合 EmbeddingService 与 MilvusStore（dense + sparse 混合检索）。

对外提供异步接口：
- index_turn:     一轮对话（用户消息 + Bot 回复）嵌入并入库
- search:         语义检索 → hybrid_search（dense+sparse，RRF 融合）
- search_by_user: 属性检索（person/content_keyword/时间窗）→ hybrid_search
- hybrid_search:  dense + sparse 候选，RRF k=60 融合，当前群优先跨群补齐
"""

import asyncio
import logging
from datetime import datetime, timedelta

from common import BotConfig
from bot.core.rag.milvus import MilvusStore, _esc
from bot.core.rag.rrf import rrf_merge

logger = logging.getLogger(__name__)

# 时间戳存储格式（TEXT，定宽零填充）：字典序 == 时间序，表达式直接 >= / <= 比较。
TS_FMT = "%Y-%m-%d %H:%M:%S"
# 每信号检索候选数（对齐旧 candidate_k=50）
CANDIDATE_K = 50


def normalize_time(text: str) -> str:
    """把 ISO 风格时间（YYYY-MM-DD / YYYY-MM-DD HH:MM:SS / T 分隔）规范成 TS_FMT。

    非法输入抛 ValueError（调用方应转成工具错误提示）。
    """
    return datetime.fromisoformat(text.strip()).strftime(TS_FMT)


def _build_expr(person: str, start_time: str, end_time: str) -> str:
    """组装 milvus 过滤表达式：时间窗 + 人名前缀（空段省略，以 ' && ' 连接）。"""
    conds = []
    if start_time:
        conds.append(f"timestamp >= '{start_time}'")
    if end_time:
        conds.append(f"timestamp <= '{end_time}'")
    if person:
        p = _esc(person)
        conds.append(f"(sender_name like '{p}%' || receiver_name like '{p}%')")
    return " && ".join(conds)


class RagService:
    """群聊历史 RAG：索引一轮对话，混合检索相关历史。"""

    def __init__(self, config: BotConfig, store: MilvusStore | None = None) -> None:
        self.config = config
        self._store = store or MilvusStore(config)

    @property
    def enabled(self) -> bool:
        return self.config.rag_enabled

    # ------------------------------------------------------------------
    # 索引
    # ------------------------------------------------------------------

    async def index_turn(
        self,
        thread_id: str,
        user_id: str,
        user_name: str,
        bot_id: str,
        bot_name: str,
        user_message: str,
        bot_reply: str,
    ) -> None:
        """嵌入并存储一轮对话（用户消息 + Bot 回复）。失败不抛出，仅降级。

        ``bot_reply`` 可为空（非回复轮）：此时只入库用户消息 1 条。
        每条记录显式建模 sender/receiver：
        - 用户消息：sender=用户(id/昵称)，receiver=bot（回复轮）或空（群广播）
        - bot 回复：sender=bot，receiver=用户
        """
        if not self.enabled:
            return
        try:
            pairs = [(user_message, "user"), (bot_reply, "assistant")]
            kept = [(c, r) for c, r in pairs if c and c.strip()]
            if not kept:
                return
            replied = bool(bot_reply.strip())
            now = datetime.now().strftime(TS_FMT)
            texts = [c for c, _ in kept]
            metadatas = []
            for _, role in kept:
                is_user = role == "user"
                metadatas.append(
                    {
                        "thread_id": thread_id,
                        "sender_id": user_id if is_user else bot_id,
                        "sender_name": user_name if is_user else (bot_name or "bot"),
                        "receiver_id": (bot_id if replied else "") if is_user else user_id,
                        "receiver_name": (bot_name if replied else "") if is_user else user_name,
                        "timestamp": now,
                    }
                )
            await self._store.add_texts(texts, metadatas)
        except Exception:
            logger.exception("RAG index_turn failed for thread %s", thread_id)

    # ------------------------------------------------------------------
    # 检索
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        thread_id: str,
        top_k: int | None = None,
        hours: int = 0,
        start_time: str = "",
        end_time: str = "",
    ) -> list[dict]:
        """语义检索（query 兼作 dense+sparse 信号）。失败返回空列表。"""
        return await self.hybrid_search(
            query=query, thread_id=thread_id,
            hours=hours, start_time=start_time, end_time=end_time,
            top_k=top_k,
        )

    async def search_by_user(
        self,
        thread_id: str | None = None,
        person: str = "",
        content_keyword: str = "",
        hours: int = 0,
        start_time: str = "",
        end_time: str = "",
        limit: int = 10,
    ) -> list[dict]:
        """按发送者/接收者昵称 + 内容关键词 + 时间窗口检索。

        ``thread_id`` 为 None 时检索**全部群**（属性检索跨群），否则限定该群。
        ``person`` 进 expr 前缀匹配发言者/接收者；``content_keyword`` 作 sparse
        信号（查"谁说过 xx"）。时间窗口二选一：``hours`` 相对窗口 /
        ``start_time``/``end_time`` 绝对边界。失败返回空列表。
        """
        return await self.hybrid_search(
            query="", thread_id=thread_id, person=person,
            content_keyword=content_keyword,
            hours=hours, start_time=start_time, end_time=end_time,
            top_k=limit,
        )

    async def hybrid_search(
        self,
        query: str,
        thread_id: str,
        person: str = "",
        content_keyword: str = "",
        hours: int = 0,
        start_time: str = "",
        end_time: str = "",
        top_k: int | None = None,
    ) -> list[dict]:
        """dense + sparse 双信号候选，RRF 融合；当前群优先，不足跨群补齐。

        - dense：query 非空才跑，候选按 ``rag_score_threshold`` 过滤；
        - sparse：``content_keyword or query`` 非空才跑，无阈值；
        - 本群融合结果不足 top_k 且 thread_id 非空 → 跨群补齐（expr 仍生效）；
        - 最终 RRF 融合后截断到 top_k。
        person-only（无 content/query）→ 返回空。
        """
        if not self.enabled:
            return []
        try:
            if hours > 0 and not start_time:
                start_time = (datetime.now() - timedelta(hours=hours)).strftime(TS_FMT)
            expr = _build_expr(person, start_time, end_time)
            limit = top_k or self.config.rag_top_k

            dense: list[dict] = []
            if query.strip():
                dense = await self._store.search_dense(query, expr, thread_id, CANDIDATE_K)
                dense = [h for h in dense if h.get("score", 0.0) >= self.config.rag_score_threshold]
            sparse_kw = content_keyword.strip() or query.strip()
            sparse: list[dict] = []
            if sparse_kw:
                sparse = await self._store.search_sparse(sparse_kw, expr, thread_id, CANDIDATE_K)

            # 当前群候选不足 → 跨群补齐（thread_id=None，expr 仍生效）
            if thread_id and len({h["id"] for h in dense + sparse}) < limit:
                if query.strip():
                    dense_x = await self._store.search_dense(query, expr, None, CANDIDATE_K)
                    dense += [h for h in dense_x if h.get("score", 0.0) >= self.config.rag_score_threshold]
                if sparse_kw:
                    sparse += await self._store.search_sparse(sparse_kw, expr, None, CANDIDATE_K)

            return rrf_merge([dense, sparse])[:limit]
        except Exception:
            logger.exception("RAG hybrid_search failed for thread %s", thread_id)
            return []

    def close(self) -> None:
        self._store.close()
```

- [ ] **Step 5: 更新工具 docstring**

把 `bot/core/tools/search_chat_history.py` 顶部 docstring 与函数 docstring 里的「走 SQL 属性检索（无 embedding）」改为「走属性检索（sparse BM25 信号，无 dense embedding）」，并更新 CLAUDE.md 同义表述（见 Task 5 Step 2，一并处理）。

- [ ] **Step 6: 运行验证通过**

Run: `uv run pytest tests/test_rag_service.py tests/test_rrf.py tests/test_milvus_store.py tests/test_search_chat_history.py tests/test_tools_factory.py tests/test_index_turn.py -v`
Expected: 全部 PASS（`test_search_chat_history` / `test_tools_factory` / `test_index_turn` 走 `StubRagService`，不受重写影响）。

- [ ] **Step 7: Commit**

```bash
git add bot/core/rag/service.py bot/core/tools/search_chat_history.py tests/fakes.py tests/test_rag_service.py
git commit -m "feat: RagService 重写为 hybrid_search（dense+sparse RRF、阈值过滤、跨群补齐）"
```

---

### Task 5: 删除旧存储层 + 接线收尾 + 全量回归

删除 sqlite-vec `RagVectorStore` 与旧库文件，更新包导出、CLAUDE.md、.gitignore、main.py 降级兜底，跑全量测试收尾。

**Files:**
- Delete: `bot/core/rag/store.py`
- Delete: `db/rag.sqlite`（运行库文件，若存在）
- Modify: `bot/core/rag/__init__.py`（导出 MilvusStore，去掉 RagVectorStore）
- Modify: `main.py`（RAG 初始化 try/except 降级）
- Modify: `.gitignore`（新增 `milvus.db`）
- Modify: `CLAUDE.md`（RAG 架构段落 + 三库表）

**Interfaces:**
- Consumes: Task 1-4 全部产出
- Produces: 可运行的全量 bot；无 `RagVectorStore` / `rag.store` / `rag.sqlite` 残留引用

- [ ] **Step 1: 更新包导出 `bot/core/rag/__init__.py`**

```python
from bot.core.rag.embedder import EmbeddingService
from bot.core.rag.milvus import MilvusStore
from bot.core.rag.service import RagService

__all__ = ["EmbeddingService", "MilvusStore", "RagService"]
```

- [ ] **Step 2: main.py RAG 初始化降级兜底**

`main.py` 里：

```python
    rag_service = RagService(config) if config.rag_enabled else None
```

改为：

```python
    rag_service = None
    if config.rag_enabled:
        try:
            rag_service = RagService(config)
        except Exception:
            logger.exception("RAG init failed; falling back to rag disabled")
            rag_service = None
```

（`build_tools` 已按 `rag_service is not None and rag_service.enabled` 决定是否挂载检索工具，`rag_service=None` 即静默降级。）

- [ ] **Step 3: .gitignore 增加 milvus.db**

在 `.gitignore` 末尾追加一行：

```gitignore
# milvus-lite embedded db
milvus.db
```

- [ ] **Step 4: 删除旧存储层与旧库文件**

```bash
git rm bot/core/rag/store.py
rm -f db/rag.sqlite
```

Run: `uv run python -c "import bot.core.rag; from bot import RagService; print('import ok')"`
Expected: 打印 `import ok`，无 `ModuleNotFoundError`（不再引用 store）。

- [ ] **Step 5: 更新 CLAUDE.md 文档**

- 架构段落：`store.py # RagVectorStore — sqlite-vec ... (rag.sqlite)` → `milvus.py # MilvusStore — milvus-lite dense+sparse 混合检索 (milvus.db)`；`service.py` 注释同步为 hybrid_search。
- 三库表：`db/rag.sqlite | RagVectorStore | ...` 行 → `./milvus.db | MilvusStore | 群聊历史向量（dense+sparse，milvus-lite 单文件）`。
- RAG 小节：把「sqlite-vec / vec0 虚拟表 / query_meta 纯 SQL / LIKE 子串」等描述更新为「milvus-lite raw pymilvus / BM25+jieba / expr 过滤 / 人名前缀匹配」；`store.search` 检索策略描述更新为 `hybrid_search`（dense 阈值 + sparse + RRF + 跨群补齐）。

- [ ] **Step 6: 全量测试回归**

Run: `uv run pytest -v`
Expected: 全部 PASS。重点确认 `test_graph.py` / `test_handler.py` / `test_call_llm_node.py` / `test_tool_node.py`（走 `StubRagService`）无回归。

- [ ] **Step 7: 残留引用检查**

Run:
```bash
grep -rn "RagVectorStore\|rag\.store\|rag\.sqlite\|sqlite_vec" --include=*.py .
```
Expected: 无输出（代码层零残留；CLAUDE.md 已更新）。

- [ ] **Step 8: Commit**

```bash
git add bot/core/rag/__init__.py main.py .gitignore CLAUDE.md
git commit -m "refactor: 删除 sqlite-vec RagVectorStore 与旧 rag.sqlite，接线 MilvusStore"
```

---

## Verification（端到端）

1. **单测**：`uv run pytest -v` 全绿（Tasks 2-4 新增的 `test_rrf.py` / `test_milvus_store.py` / `test_rag_service.py` + 存量 graph/handler/tools 测试无回归）。
2. **手动跑 bot 验证检索闭环**：
   - `uv run python main.py` 从项目根启动，确认日志出现 `MilvusStore ready (uri=./milvus.db, collection=chat)`（首次自动建集合）与 `Created milvus collection 'chat'`。
   - 在群里让 bot 产生几轮带回复的对话（触发 `index_turn` 入库）。
   - 问一个过去话题的语义问题（走 `search_dense`+`search_sparse`+RRF）；再问「某人说过什么」/「谁说过 xx」+ 时间窗（走属性检索，跨群）。确认回复引用历史消息、跨群结果带 `[来源群]` 标签。
3. **降级路径**：临时把 `db/` 改名或制造 milvus 不可用，确认 bot 仍启动（RAG 静默降级，无检索工具），对话不崩。
