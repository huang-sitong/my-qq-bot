# 消息处理多 Worker 并发 — 设计文档

日期：2026-08-12
状态：待评审

## 背景

当前 `MessageHandler` 用 `asyncio.Queue` 解耦消息接收与处理，但只启动一个 worker task：

- 所有消息进入同一个队列，入口 `handle()` 只做校验和入队，能快速承接突发消息。
- `_worker()` 依次取消息，并按 `thread_id` 加锁后执行 `_process()`。
- 由于 worker 只有一个，所有频道/会话的消息在 LangGraph 执行层面仍是全局串行。
- `graph.ainvoke()` 内部虽然有 async I/O 可以让出事件循环，但同一时刻只有一个会话能调用 LLM、RAG、记忆等耗时服务。

因此当前架构的优势是“入口不阻塞 + 同会话不乱序”，瓶颈是“处理吞吐 = 1 个 worker 的吞吐”。

## 目标

1. 把单 worker 改为可配置的 N 个 asyncio worker。
2. 不同 `thread_id` 的消息可以并发处理。
3. 同一个 `thread_id` 的消息严格按入队顺序串行处理。
4. 支持可选有界队列，持续过载时通过阻塞入队产生背压，避免无限内存积压。
5. 不改变现有消息分类、命令分发、LangGraph 图、RAG、回复策略等业务语义。

## 非目标

- 不使用 Python `threading.Thread` 替代 asyncio worker。
- 不为每条消息创建一个 task 或 thread。
- 不为每个 `thread_id` 常驻一个 worker/queue。QQ 群和私聊频道数量可能很大，常驻资源不可控。
- 不做运行时动态扩缩容，worker 数量只从配置读取。
- 不做持久化消息队列、跨进程分发、多机水平扩展。
- 不改变 checkpoint、memory、RAG 的数据结构。
- V1 不加入消息丢弃策略；过载策略只提供“有界队列 + 入队背压”。

## 决策

| 决策 | 结论 | 理由 |
|---|---|---|
| 并发单位 | asyncio `Task`，即 worker 协程 | 当前链路全是 async；多 task 可并发等待 LLM/HTTP/SQLite 等 I/O，不需要 OS 线程 |
| 并发粒度 | 共享队列 + N 个 worker + 每 `thread_id` 一把 `asyncio.Lock` | 队列 FIFO 与 lock 保持同会话顺序；不同会话可并行 |
| worker 数量配置 | `BOT_MESSAGE_WORKER_COUNT`，默认 1，合法范围 1..64 | 默认保持现行为；需要高并发时显式调大 |
| 队列容量配置 | `BOT_MESSAGE_QUEUE_MAXSIZE`，默认 0，0 表示无界 | 默认兼容现状；设置正整数后启用入队背压 |
| 动态扩缩容 | V1 不做 | 启动时创建固定 worker 池，实现和测试边界清晰 |
| 每 `thread_id` 常驻 worker | 不做 | 频道数量多时 task/queue 数量不可控，需额外回收机制，超出本次范围 |
| 并发安全 | 对共享服务做并发安全审查；不安全的底层访问加最小粒度锁/信号量 | 多 worker 会使 `_process` 并发执行，不能默认所有服务线程安全 |

## 架构

```text
WS 事件
  → MessageHandler.handle()
      → 空消息过滤 + thread_id 组装
      → asyncio.Queue.put()
                │
                ▼
        ┌─────────────┬─────────────┐
        │ worker 0    │ worker 1    │ ... worker N-1
        └─────────────┴─────────────┘
                │
                ▼
        thread_id → asyncio.Lock
                │
                ▼
        _process()
          → 命令分发（图外）
          → graph.ainvoke()
          → send_reply()
```

同一 `thread_id` 的消息无论由哪个 worker 取出，都必须先拿到该 `thread_id` 的锁；拿到锁后同一会话的后续消息会排队等待，从而保持顺序。

## 数据流

### 入队

`handle()` 保持现状：

1. 空消息直接返回。
2. 计算 `platform:guild:channel` 作为 `thread_id`。
3. `await self._queue.put(...)`。

当 `BOT_MESSAGE_QUEUE_MAXSIZE > 0` 时，队列满后 `put` 会等待，从而阻塞 WebSocket 接收循环，形成背压。该行为需要写入文档：不是丢弃消息，而是让上游放慢投递。

### 消费

每个 worker 循环：

```text
item = await queue.get()
if item is None: exit
thread_id = item["thread_id"]
lock = self._locks.setdefault(thread_id, asyncio.Lock())
async with lock:
    await self._process(item)
queue.task_done()
```

单消息异常继续由 `_process` 外层捕获并记录，不影响 worker 存活。

### 关闭

`stop()` 改为向队列放入 `worker_count` 个 `None` sentinel，然后 `await asyncio.gather(*worker_tasks)`。由于 sentinel 放在队列末尾，worker 会先处理完已入队消息再退出。

## 配置

### `common/config.py`

新增字段：

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

配置语义：

- `BOT_MESSAGE_WORKER_COUNT=1`：与当前行为一致。
- `BOT_MESSAGE_WORKER_COUNT=4`：最多 4 个不同会话同时执行 `_process()`。
- `BOT_MESSAGE_QUEUE_MAXSIZE=0`：无界队列，保持现状。
- `BOT_MESSAGE_QUEUE_MAXSIZE=1024`：队列积压达到 1024 时，`handle()` 的 `put` 阻塞。

### `.env-template`

新增：

```text
# --- Message concurrency ---
# BOT_MESSAGE_WORKER_COUNT = 1   # asyncio worker 数量；>1 时不同 thread_id 可并发处理，同一会话仍串行
# BOT_MESSAGE_QUEUE_MAXSIZE = 0  # 消息队列上限；0=无界，正整数=满时入队阻塞产生背压
```

## 实现变更

### `bot/handler.py`

- `__init__` 新增 `worker_count: int = 1` 与 `queue_maxsize: int = 0`。
- `_queue` 使用 `asyncio.Queue(maxsize=queue_maxsize)`。
- 把 `_worker_task` 改为 `_worker_tasks: list[asyncio.Task]`。
- `start()` 创建 `worker_count` 个 `self._worker()` task，并记录日志。
- `stop()` 放入 `worker_count` 个 sentinel，等待所有 worker 退出后清空列表。
- `_worker()` 保留 `thread_id` lock 逻辑；外层加一个异常兜底，避免单次循环异常导致 worker task 提前退出。
- `_process()` 不改业务逻辑。

示例：

```python
async def start(self) -> None:
    self._worker_tasks = [
        asyncio.create_task(self._worker())
        for _ in range(self._worker_count)
    ]
    logger.info("Message workers started: %d", self._worker_count)

async def stop(self) -> None:
    for _ in range(self._worker_count):
        await self._queue.put(None)
    if self._worker_tasks:
        await asyncio.gather(*self._worker_tasks, return_exceptions=True)
        self._worker_tasks = []
```

### `main.py`

构造 `MessageHandler` 时传入：

```python
handler = MessageHandler(
    client,
    graph,
    persona,
    api_client,
    bot_config=config,
    command_registry=command_registry,
    command_services=command_services,
    worker_count=config.message_worker_count,
    queue_maxsize=config.message_queue_maxsize,
)
```

### 文档

- `AGENTS.md`：把“worker（按 thread_id 锁串行）”更新为“worker 池（N 个 asyncio worker，按 thread_id 锁串行）”。
- `.env-template`：新增上述两个配置。

## 并发安全

启用多 worker 后，不同 `thread_id` 的 `_process()` 会同时运行，需要审查以下共享服务：

- `AsyncSqliteSaver`：官方实现自带 `asyncio.Lock`，并发 checkpoint 读写会串行化；仍需要 `thread_id` lock 保证同一会话的读取/写入顺序。
- `httpx.AsyncClient`：`SatoriApiClient` 与 `VisionService` 使用的 async client 可并发执行请求。
- `MemoryStore`：`AsyncSqliteStore` 的调用应做并发验证；若官方 store 不保证并发，则在 `MemoryStore` 内加一把 `asyncio.Lock` 包裹读写。
- `MilvusStore`：`pymilvus.MilvusClient` 经 `asyncio.to_thread` 执行，多 worker 下可能并发访问。若 `MilvusClient` 非线程安全，在 `MilvusStore` 内加最小粒度 `asyncio.Lock` 包裹 client 操作。
- `EmbeddingService` / `OllamaEmbeddings`：`asyncio.to_thread` 可能并发调用同一 embedder；如果 Ollama 并发调用不安全或会导致负载失控，用 `asyncio.Semaphore` 限制并发嵌入请求。
- `ChatOpenAI`：async LLM 客户端本身支持并发请求；是否同时发起多个 LLM 调用由 `worker_count` 决定，用户应结合 API 限流设置。

并发安全审查是验收条件，不是可选项。若某项底层库无法确认安全，采取“服务内加锁/信号量”而不是“退回单 worker”。

## 顺序保证

同一 `thread_id` 的顺序依赖两点：

1. `asyncio.Queue` FIFO：先入队的消息先被 worker 取出。
2. `asyncio.Lock` fair scheduling：同一 `thread_id` 的等待者按注册顺序被唤醒。

该保证需用测试锁定。即使 worker 数量大于同一会话的待处理消息数，同一会话也不会并行执行。

## 错误处理

- 单条消息处理异常：与现状一致，`_worker` 内 `try/except` 记录日志，不中断其他消息。
- worker 循环异常：外层 `try/except` 记录日志后继续下一次取消息，避免 worker task 提前死亡。
- shutdown：sentinel 数必须等于 worker 数，否则会有 worker 永久等待。
- 队列满：`handle()` 阻塞在 `put`，通过 WebSocket 接收循环形成背压；不丢弃消息。

## 测试

### 配置测试

- `BOT_MESSAGE_WORKER_COUNT` 默认 1，非法值 0/负数/65 抛 `ValidationError`。
- `BOT_MESSAGE_QUEUE_MAXSIZE` 默认 0，非法负数抛 `ValidationError`。

### Handler worker 池测试

- `start()` 创建 N 个 worker task，`stop()` 后全部退出。
- worker 数量为 1 时保持现有行为。
- 单消息异常不影响后续消息处理。

### 顺序测试

构造带延迟的 stub graph，启动 N=3 worker，向同一 `thread_id` 连续入队 3 条消息，断言 graph 的 `ainvoke` 调用顺序严格为入队顺序。

### 并发测试

构造一个可阻塞的 stub graph，启动 N=2 worker：

- 第一条消息进入 graph 后阻塞；
- 第二条不同 `thread_id` 的消息仍可进入 graph；
- 同 `thread_id` 的第二条消息不能进入 graph，直到第一条完成。

测试应使用 `asyncio.Event` 控制时序，避免依赖真实网络。

### 队列背压测试

- `queue_maxsize=1` 时，队列已满后 `handle()` 不立即返回；
- worker 消费一条后，`handle()` 恢复。

### 现有回归

- `tests/test_handler.py` 全部保持通过。
- 命令分发、图外指令、RAG 索引路径不受 worker 数量影响。

## 验收标准

1. `BOT_MESSAGE_WORKER_COUNT=1` 时行为与当前版本一致。
2. `BOT_MESSAGE_WORKER_COUNT>1` 时，不同 `thread_id` 可同时进入 `_process()`。
3. 同一 `thread_id` 的消息始终按入队顺序串行。
4. 队列满时有明确背压，不丢消息、不无限增长内存。
5. 并发安全审查完成，未发现共享服务的数据竞争；不安全的底层访问已加锁/限流。
6. 新测试覆盖 worker 池、顺序、并发、背压和现有回归。
