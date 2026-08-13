# RAG 迁移到 milvus-lite + raw pymilvus（dense + sparse 混合检索）

- 日期: 2026-08-08
- 状态: 设计已批准，待实现

## Context（为什么做这个变更）

当前 RAG（`bot/core/rag/`）用 **sqlite-vec** 做纯语义（dense）向量检索。两个问题：

1. **纯语义对精确词召回差**：人名 / 专有名词 / ID / 精确短语这类词，语义向量容易漏。
2. **sqlite-vec 能力弱**：vec0 虚拟表不支持元数据过滤，时间窗靠检索后剪枝；跨群补齐、per-thread 淘汰、属性检索全部手工 SQL。

目标：把 RAG 存储层迁移到 **milvus-lite**（嵌入式 Milvus，**raw pymilvus 直连**），原生支持 dense（语义）+ sparse（BM25 词法）双信号，**自研 RRF 融合**以提升召回。中文分词用 milvus 内置 **jieba** 分析器。

**本期范围**：仅完成聊天记录的 3 种检索（`search_dense` / `search_sparse` / `hybrid_search`）。文档知识库（上传 + 分块入库）为**未来扩展**，本期不实现。

## 已实机验证的事实（原生 Windows，milvus-lite 3.2.0 + pymilvus 3.0.1）

- milvus-lite 3.2.0 在原生 Windows 上可运行。
- **raw pymilvus 全链路跑通**：`create_collection`（BM25 内置函数 + jieba 分析器 + partition key + 双索引）→ `insert`（text 进 BM25 函数自动出 sparse）→ `search`（`anns_field='vector'/'sparse'` 字段级）→ `query`（动态字段 expr）。
- **三个实测踩坑**：
  1. BM25 函数输出字段 `sparse` 必须**先在 schema 声明**（`DataType.SPARSE_FLOAT_VECTOR`），再 `add_function`；
  2. analyzer 配在 **`text` 字段**上（`enable_analyzer=True` + `analyzer_params={"tokenizer":"jieba"}`），**不是** Function 的 params；
  3. 按线程隔离用 **expr `thread_id == 'X'`**（partition key 自动路由优化），**不是** `partition_names`（那是分区名非键值）。
- **弃用 langchain-milvus**：`similarity_search` 在双向量集合走内部 `_collection_hybrid_search`（跨调用状态污染 bug）；`similarity_search_by_vector` 实测抛 `AssertionError: _collection_search does not support multi-vector`；无公开字段级检索。raw pymilvus 完全覆盖需求且更可控。
- 同时存聊天 + 文档实测可行（`kind` 字段区分），但按 D7 本期不加。
- milvus 的 jieba 分析器需要 Python `jieba` 包；不需要 `pymilvus[model]`。

## 决策记录

| # | 决策点 | 选择 |
|---|---|---|
| D0 | 存储后端 | milvus-lite，**raw pymilvus 直连**（弃用 langchain-milvus 高层封装） |
| D1 | 混合检索 | **自研 RRF**：`search_dense` + `search_sparse` 字段级检索，`RagService.hybrid_search` 合并 |
| D2 | `content_keyword` 映射 | 走 **sparse BM25** 检索（替代 SQL `LIKE %kw%` 子串） |
| D3 | 旧 `rag.sqlite` 数据 | **直接删除旧数据文件**（不迁移、不保留） |
| D4 | 中文分词 | milvus 内置 jieba 分析器（`{'tokenizer': 'jieba'}`），不装 pkuseg |
| D5 | 检索 API | 3 种检索：`search_dense` / `search_sparse` / **自研 `hybrid_search`**；**不用 `client.query` 作检索**（person/content 过滤并入 expr 传参） |
| D6 | milvus 连接 | `MilvusClient(uri="./milvus.db")`（bot 从项目根运行时落 `F:\PythonProject\qq-bot\milvus.db`） |
| D7 | 文档知识库 | **未来扩展**（届时加 `kind`/`doc_id`/`doc_title`/`chunk_index` 等字段或独立 doc collection）；本期仅聊天 3 种检索 |

## 架构

### 组件（`bot/core/rag/`）

| 文件 | 动作 | 职责 |
|---|---|---|
| `milvus.py` | 新增 | `MilvusStore`——raw pymilvus：建集合、insert、字段级检索、淘汰 |
| `embedder.py` | **不变** | `EmbeddingService.embed_query` / `embed_documents` 直接复用（Instruct 前缀 + 磁盘缓存） |
| `service.py` | 重写 | `RagService`——`index_turn` 写 MilvusStore；`search`/`search_by_user` 统一委托自研 `hybrid_search` |
| `store.py` | 删除 | sqlite-vec `RagVectorStore` 移除 |
| `cache.py` | 不变 | `EmbeddingCache` |

### 集合创建（raw pymilvus，已实测）

```python
from pymilvus import MilvusClient, DataType, Function, FunctionType

client = MilvusClient(uri="./milvus.db")          # D6

schema = client.create_schema(auto_id=True, enable_dynamic_field=True)
schema.add_field("pk", DataType.INT64, is_primary=True)
schema.add_field("vector", DataType.FLOAT_VECTOR, dim=config.embed_dimensions)   # dense
schema.add_field("sparse", DataType.SPARSE_FLOAT_VECTOR)                         # 踩坑1：先声明输出字段
schema.add_field("text", DataType.VARCHAR, max_length=65535,
                 enable_analyzer=True, analyzer_params={"tokenizer": "jieba"})   # 踩坑2：analyzer 在字段上
schema.add_field("thread_id", DataType.VARCHAR, max_length=128, is_partition_key=True)
schema.add_function(Function(name="bm25_fn", function_type=FunctionType.BM25,
                             input_field_names=["text"], output_field_names=["sparse"]))

index_params = client.prepare_index_params()
index_params.add_index("vector", index_type="HNSW", metric_type="COSINE", params={"M": 8, "efConstruction": 64})
index_params.add_index("sparse", index_type="SPARSE_INVERTED_INDEX", metric_type="BM25")
client.create_collection("chat", schema=schema, index_params=index_params)
```

- 集合 schema（聊天）：

| 字段 | 类型 | 来源 |
|---|---|---|
| `pk` | int64 auto | 主键 |
| `vector` | float[embed_dim] | dense，`EmbeddingService.embed_documents` |
| `sparse` | sparse vector | `FunctionType.BM25`，从 `text` 自动生成 |
| `text` | varchar(max_length=65535, enable_analyzer) | 内容（含 `[图片：{desc}]` 变体） |
| `thread_id` | varchar | **partition key**（按线程隔离） |
| `sender_id/sender_name/receiver_id/receiver_name/timestamp` | 动态字段 | 元数据（expr 可过滤，已验证） |

- 线程隔离用 expr `thread_id == 'X'`（踩坑3），partition key 自动路由到对应分区。
- D7 扩展点：知识库时新增 `kind` / `doc_id` / `doc_title` / `chunk_index` / `owner_id` 字段，本期不建。

### MilvusStore（`bot/core/rag/milvus.py`）

```python
class MilvusStore:
    def __init__(self, config, uri="./milvus.db", collection="chat") -> None: ...   # 建 client + 若不存在建集合
    async def add_texts(self, texts: list[str], metadatas: list[dict]) -> None: ... # embed_documents + client.insert
    async def search_dense(self, query: str, expr: str, thread_id: str | None, k: int) -> list[dict]: ...
    async def search_sparse(self, query: str, expr: str, thread_id: str | None, k: int) -> list[dict]: ...
    async def prune(self, thread_id: str, keep: int) -> None: ...
    def close(self) -> None: ...
```

- `add_texts` → `vecs = await embedder.embed_documents(texts)`；行 = `{**metadata, "vector": vec, "text": text}` → `client.insert(collection, rows)`（text 进 BM25 函数自动出 sparse）。
- `search_dense(query, expr, thread_id, k)` → `vec = await embedder.embed_query(query)`；`client.search(cn, data=[vec], anns_field='vector', filter=_build_filter(expr, thread_id), limit=k, output_fields=[sender/receiver/content/timestamp/thread_id])`。
- `search_sparse(query, expr, thread_id, k)` → `client.search(cn, data=[query], anns_field='sparse', filter=_build_filter(...), limit=k, output_fields=[...])`（文本直接进 BM25 函数）。
- `_build_filter(expr, thread_id)`：`expr` 非空且 `thread_id` 非 None → `expr && thread_id == 'X'`；仅 thread_id → `thread_id == 'X'`；否则 expr（跨群）。字符串值做 `'` 转义。
- `prune(thread_id, keep)` → `client.query` 枚举 pks 按 timestamp 排序，删最旧超限（事务性家务，非检索 API）。
- 全部操作 try/except 降级，失败不崩图。

### expr 组装（`RagService._build_expr` 纯函数）

- 时间窗：`timestamp >= '{start}' && timestamp <= '{end}'`（ISO 定宽串，空则省略该段）。
- person：`(sender_name like '{person}%' || receiver_name like '{person}%')`。
- 合并：非空段以 ` && ` 连接。

### RagService.hybrid_search（`bot/core/rag/service.py`，自研 RRF）

```python
async def search(self, query, thread_id, hours=0, start_time="", end_time="", top_k=None) -> list[dict]:
    return await self.hybrid_search(query=query, thread_id=thread_id, hours=hours, start_time=start_time, end_time=end_time, top_k=top_k)

async def search_by_user(self, thread_id=None, person="", content_keyword="", hours=0, start_time="", end_time="", limit=10) -> list[dict]:
    return await self.hybrid_search(query="", thread_id=thread_id, person=person, content_keyword=content_keyword, hours=hours, start_time=start_time, end_time=end_time, top_k=limit)

async def hybrid_search(self, query, thread_id, person="", content_keyword="", hours=0, start_time="", end_time="", top_k=None) -> list[dict]:
    expr = _build_expr(person, start_time, end_time)
    dense  = await store.search_dense(query, expr, thread_id, CANDIDATE_K)          # query 非空才跑
    sparse = await store.search_sparse(content_keyword or query, expr, thread_id, CANDIDATE_K)
    if 合并(dense, sparse) 结果数 < top_k:                                            # 跨群补齐
        dense  |= await store.search_dense(query, expr, None, CANDIDATE_K)
        sparse |= await store.search_sparse(content_keyword or query, expr, None, CANDIDATE_K)
    return rrf_merge(dense, sparse, k=60)[:top_k]
```

- `CANDIDATE_K = 50`（对齐现 `candidate_k=50`）；RRF 常数 `k=60`：`score(doc) = Σ 1/(60 + rank_i)`。
- dense 候选按 `BOT_RAG_SCORE_THRESHOLD` 过滤；sparse 无阈值。
- `query` 为空（属性路径）：跳过 dense 信号，仅 sparse；person/content 过滤进 expr。**person-only（无 content/query）返回空**——工具 docstring 引导 LLM 搭配主题。

### 数据流

**索引**（`index_turn`，图内 action_node）：

- 内容（含图片描述变体）→ `MilvusStore.add_texts`（insert，dense + sparse 一次写入）。
- 元数据：`sender_id/name`、`receiver_id/name`（bot 回复轮 sender=bot）、`timestamp`、`thread_id`。
- 纯媒体且无回复仍跳过（沿用现有 `index_turn` 判定）。

**检索**（`search_chat_history`）——`RagService.search` / `search_by_user` 统一委托 **`hybrid_search`**（D5，不用 `client.query` 作检索）。

- **语义路径**（无 user_name/content_keyword）：`query` 作 dense+sparse 双信号。
- **属性路径**（有 user_name/content_keyword）：person 过滤进 expr；`content_keyword` 作 sparse 信号；`query` 可选作 dense 信号——回答「某人说过关于 X 的话」。
- 时间窗 expr 全程生效。
- 跨群结果渲染 `[来源群]` 标签逻辑保留（从元数据 thread_id 取 guild 段）。
- **行为变化**：人名为前缀匹配（原 SQL 子串）；`content_keyword` 为 BM25 相关度（原 LIKE 子串）。

### 错误处理

- `MilvusStore` 全部操作包 try/except：写失败跳过索引、搜失败返回空，**绝不崩图**。
- milvus-lite 惰性连接（首连开 `./milvus.db`）；不可用时 RAG 静默降级（等同 `rag_enabled=false`）。
- 工具层沿用现有 `build_tools` / `_tool_error_message` 降级机制（工具失败 → 「工具执行失败。」）。

### Retention

- 每 thread 超过 `rag_retention_per_thread` 时：`client.query(expr="thread_id == '...'", output_fields=['pk','timestamp'])` 按 timestamp 排序，取最旧超限 pks → `client.delete(filter="pk in [...]")`。
- 沿用现有「按 timestamp DESC 淘汰最旧」语义。

## 依赖 / 配置变化

- `pyproject.toml`：**删 `sqlite-vec`**；加 `pymilvus`、`milvus-lite`、`jieba`（不加 langchain-milvus）。
- **删除 `db/rag.sqlite`**（sqlite-vec 旧库文件）；新增 `./milvus.db`（milvus-lite 单文件，D6）。
- `BOT_RAG_*` 配置语义不变：`BOT_RAG_TOP_K`（最终 top-N）、`BOT_RAG_SCORE_THRESHOLD`（dense 候选阈值）、`BOT_RAG_RETENTION_PER_THREAD`（淘汰）、`BOT_RAG_MAX_AGENT_ROUNDS`（工具轮次）、`BOT_EMBED_*`（嵌入不变）。

## 测试

| 文件 | 动作 | 覆盖 |
|---|---|---|
| `test_rag_store.py` | 重写为 `test_milvus_store.py` | tmp uri 库上 add / dense / sparse / expr 过滤 / 线程隔离 / 时间窗 / prune |
| `test_rag_service.py` | 适配 MilvusStore | index_turn 双写、search RRF 结果、属性检索、降级 |
| `test_rrf.py` | 新增 | RRF 合并单测（toy rankings、k=60、交叉信号） |
| `test_embed_cache.py` | 不变 | embedder 行为保留 |
| graph/handler/tools 测试 | 基本不动 | 用 `StubRagService`，`RagService` 接口保持稳定 |

## 实施顺序（草案）

1. pyproject 依赖增删 + `uv sync` 验证 milvus-lite 可跑。
2. `milvus.py` `MilvusStore`（raw pymilvus）+ `test_milvus_store.py`。
3. `service.py` 重写（hybrid_search / RRF / 补齐 / 淘汰）+ `test_rag_service.py` / `test_rrf.py`。
4. `search_chat_history.py` / `search_by_user` 接线 + 工具测试适配。
5. `graph.py` / `main.py` wiring（`RagVectorStore` → `MilvusStore`），删除 `store.py`，并**删除旧 `db/rag.sqlite` 文件**。
6. 全量测试 + 手动跑 bot 验证检索闭环。
