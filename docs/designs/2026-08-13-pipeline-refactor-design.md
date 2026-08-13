# QQ Bot 消息流水线裁剪与后台 RAG 索引设计

日期：2026-08-13

状态：待评审

## 背景与目标

当前消息处理把 RAG 索引和上下文压缩放在 LangGraph 内，回复路径必须等
`summarize` 与 `index_turn` 完成才结束。其中 RAG 索引包含向量模型调用和
Milvus 写入，即使已经使用 `asyncio.to_thread`，也会延长同一消息 worker 的
持有时间，并增加 graph 的 superstep 数量。

目标：

- 协议事件与领域事件分离，核心流程不再直接依赖 Satori `EventBody`。
- 路由与执行分离，由 Router 决策，handler 只负责归一化、调度和执行。
- 缩小 graph，只保留 LLM 回复路径。
- graph 一产出回复后立即发送并放回消息队列。
- RAG 索引移到后台 IndexWorker，向量调用和 Milvus 写入继续走线程池。
- 上下文压缩移到下一轮处理前，不阻塞当前回复，也不与同会话 checkpoint
  写入竞争。
- 本地 SQLite checkpoint 保持不变，不做分布式状态。

## 非目标

- 不引入 Kafka/RabbitMQ/Pulsar 等消息总线。
- 不引入跨进程幂等去重、事件存储或 schema registry。
- 不做分布式 checkpoint 或共享状态存储。
- 不新增 LLM/MCP 重试、熔断、死信队列等失败边界。
- 不改 MCP、skills、commands 的现有数据模型。
- 不迁移现有 DB 数据。

## 当前流程

```text
Satori EventBody
  -> MessageHandler.handle()
  -> 消息队列
  -> worker
  -> parse_content + command 判断
  -> graph.ainvoke
       detect_intent
       describe_image
       call_llm
       tools -> skill_manager -> call_llm
       summarize
       index_turn
  -> 图外发送 reply
```

问题：

- `index_turn` 在 graph 内等待向量模型调用和 Milvus 写入。
- `summarize` 可能触发第二次 LLM 调用，阻塞当前回复。
- `detect_intent`、`summarize`、`index_turn` 增加 graph superstep 和
  checkpoint 写入次数。
- handler 同时承担协议归一化、路由、执行和回复，职责偏重。

## 目标流程

```text
Satori EventBody
  -> IncomingMessage
  -> 消息队列
  -> worker
  -> thread lock
  -> Router 决策

command      -> 命令 handler -> 回复
reply        -> compact_if_needed -> 最小 graph -> 立即回复 -> IndexWorker
context_only -> compact_if_needed -> aupdate_state 写入 HumanMessage -> IndexWorker
ignore       -> 结束
```

最小 graph：

```text
START
  -> describe_image
  -> call_llm
  -> tools -> skill_manager -> call_llm
  -> END
```

## 组件设计

### IncomingMessage

新增 `object/bot/message.py`，提供协议无关的领域事件：

```python
@dataclass(frozen=True)
class IncomingMessage:
    event_id: str
    platform: str
    thread_id: str
    channel_id: str
    channel_type: int
    user_id: str
    user_name: str
    raw_content: str
    content_kind: str
    has_text: bool
    llm_text: str
    clean_text: str
    mentions: dict[str, str]
    image_srcs: list[str]
```

`MessageHandler.handle()` 只负责把 `EventBody` 归一化为 `IncomingMessage`
并入队。CLI、测试和后续协议适配都不需要接触 Satori 事件对象。

### Router

新增 `bot/core/router.py`，提供纯路由决策：

```text
command
reply
context_only
ignore
```

决策复用现有 `decide_reply`、`keep_in_context`、`parse_command`。Router 不
调用 graph、不发送回复、不写 checkpoint，只返回 `RouteDecision`。

`reply` 与 `context_only` 路径所需的 `HumanMessage` 由执行层构造，避免
Router 依赖 LangChain。`compact_if_needed` 只在 Router 判定为
`reply` 或 `context_only` 后执行，command 和 ignore 不触发压缩。

### compact_if_needed

新增 `bot/core/compaction.py`，把当前 graph 内压缩逻辑提取为图外 helper：

```text
graph.aget_state(thread_id)
  -> estimate_context_tokens(...)
  -> 超阈值
  -> summarize_node(force=True)
  -> graph.aupdate_state(...)
```

压缩发生在消息 worker 持有同一 thread lock 时，因此不会和同会话
`graph.ainvoke` 或 `aupdate_state` 并发写 checkpoint。

`context_tokens` 不新增为 `BotState` 持久化字段。它是派生值，统一由
`estimate_context_tokens` 按需计算，避免写入 checkpoint 后过期。

### 最小 Graph

`bot/core/graph.py` 移除以下节点：

- `detect_intent`：决策由 Router 承担，HumanMessage 由执行层构造。
- `summarize`：压缩由 `compact_if_needed` 承担。
- `index_turn`：索引由 `IndexWorker` 承担。

保留：

- `describe_image`
- `call_llm`
- `tools`
- `skill_manager`

回复路径的 HumanMessage 通过 `graph.ainvoke` 输入传入，依赖
`BotState.messages` 的 `add_messages` reducer 与 checkpoint 旧消息合并。
实现前用测试锁定该行为；若 LangGraph 版本不合并输入消息，则保留一个最小
`prepare_turn` 节点完成消息追加，不回退到完整 `detect_intent`。

### IndexWorker

新增 `object/bot/index_task.py` 与 `bot/core/rag/index_worker.py`：

```python
@dataclass(frozen=True)
class IndexTurnTask:
    thread_id: str
    user_id: str
    user_name: str
    bot_id: str
    bot_name: str
    user_message: str
    bot_reply: str
```

`IndexWorker` 使用独立 `asyncio.Queue` 消费 `IndexTurnTask`，调用现有
`RagService.index_turn()`。

handler 生成任务时按当前 `index_turn_node` 的规则预计算 `user_message`：
图片消息在文本后追加 `[图片]` 占位符，`bot_reply` 来自 graph 返回的
`reply_text`，context_only 路径的 `bot_reply` 为空。

V1 使用单个 FIFO consumer，保证同一会话索引顺序。向量模型调用和 Milvus
写入继续由现有 `EmbeddingService` 和 `MilvusStore` 内部的
`asyncio.to_thread` 执行，不新增 ThreadPoolExecutor。

RAG 是可重建缓存，任务可丢弃。队列关闭时未消费任务只记日志。

### MessageHandler

handler 改为三部分：

- ingress：`handle()` 归一化 `IncomingMessage`。
- worker：持有 thread lock，执行 `compact_if_needed` 和 Router。
- executor：根据 `RouteDecision` 执行命令、graph、checkpoint 更新或忽略。

回复路径执行顺序：

```text
graph.ainvoke
  -> result["reply_text"]
  -> 立即 send_message
  -> enqueue IndexTurnTask
```

context_only 执行顺序：

```text
graph.aupdate_state({"messages": [HumanMessage(...)]})
  -> enqueue IndexTurnTask(bot_reply="")
```

## 并发与顺序

- 消息 worker 继续按 `thread_id` 加锁，串行处理同一会话。
- `compact_if_needed` 在同一 thread lock 内同步执行，避免 checkpoint 竞争。
- reply 和 context_only 的 RAG 索引进入独立 IndexWorker，不占用消息 worker。
- IndexWorker 默认 FIFO，保持同一会话 RAG 写入顺序。
- 不同 thread 可以同时处理消息，不同 RAG 任务由现有 Milvus 客户端锁串行。

## 失败处理

- 压缩 LLM 失败：记录日志，跳过压缩，继续当前消息。
- graph 失败：维持现状，不发送回复。
- RAG 索引失败：沿用 `RagService` 吞异常降级，IndexWorker 只记录日志。
- RAG 队列关闭时未消费任务：记录日志并丢弃。
- 进程退出前先停消息 worker，再 drain IndexWorker，最后关闭 RagService。

## 生命周期

`main.py` 变更：

```text
创建 IndexWorker
handler.start()
index_worker.start()

运行

handler.stop()
index_worker.stop()
rag_service.close()
```

## 测试策略

新增单元测试：

- `IncomingMessage` 归一化测试。
- Router 决策表测试。
- `compact_if_needed` 阈值、写回、失败降级测试。
- IndexWorker 入队、FIFO、关闭、失败降级测试。
- 最小 graph 的 HumanMessage 合并、工具回环、技能、图片测试。

新增集成测试：

- 真实 graph + 真实 checkpointer + fake IndexWorker。
- reply 路径先返回回复再生成索引任务。
- context_only 路径写入 checkpoint 并生成索引任务。
- ignore 路径无 checkpoint、无索引任务。
- command 路径不进 graph、不生成索引任务。

现有测试调整：

- `test_graph.py` 中 graph 后索引 RAG 的断言移到 handler/IndexWorker 测试。
- `test_handler.py` 的 StubGraph 适配新输入形状。
- `/compact` 测试复用 `compact_if_needed`，保持行为一致。

## 改造顺序

1. 新增 `IncomingMessage`、`IndexTurnTask` 和 `object/__init__.py` 懒加载
   映射，不接行为。
2. 抽出 Router 纯函数，handler 仍走旧 graph，行为不变。
3. 新增 `compact_if_needed`，从 graph 移除 `summarize` 节点。
4. 新增 IndexWorker，从 graph 移除 `index_turn` 节点，handler 生成索引任务。
5. Router 构造 HumanMessage，handler 以输入消息调用最小 graph，移除
   `detect_intent` 节点。

每步保持测试通过。

## 风险与决策

- LangGraph 输入消息与 checkpoint 的合并行为需要先用测试锁定；若不成立，
  使用最小 `prepare_turn` 节点兜底。
- `summarize_node` 从 graph 节点变为图外 helper 后，仍保留原模块，避免
  `/compact` 行为漂移。
- RAG 后台任务不保证进程崩溃后不丢失，符合 RAG 是可重建缓存的定位。
- V1 不新增环境变量；IndexWorker 单消费者已满足当前规模，多消费者留待以后。

## 影响文件

新增：

- `object/bot/message.py`
- `object/bot/index_task.py`
- `bot/core/router.py`
- `bot/core/compaction.py`
- `bot/core/rag/index_worker.py`
- 对应测试文件

修改：

- `object/__init__.py`
- `bot/handler.py`
- `bot/core/graph.py`
- `main.py`
- 相关现有测试

## 验收标准

- graph 不再执行 RAG 索引或上下文压缩节点。
- 回复路径在 graph 返回后立即发送 reply，不等待 RAG 索引。
- context_only 文本仍进入 checkpoint 并生成 RAG 索引任务。
- command 路径行为不变，不进 graph、不索引。
- 同会话 checkpoint 操作串行，无后台压缩写竞争。
- 本地 SQLite checkpoint 继续作为唯一会话状态存储。
