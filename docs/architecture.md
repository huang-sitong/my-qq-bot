# qq-bot 目标架构设计（v2）

> 本文是对当前项目架构的扩写与重构方案。目标是在 **`src/bot/package/`** 路径下
> 建立应用包主体，消除 `common` 的“杂物间”语义、把 Satori 协议代码收敛到
> `platform`，把工具/MCP 代码独立成包。
>
> 路径约定：应用主体位于 `src/bot/package/`；`src/bot/__init__.py` 只作轻量包门面。

---

## 0. 当前实现状态

代码已按本目录结构迁移：`core/pipeline/utils/platform/config/tools/mcp` 以及
`commands/conversation/domain/knowledge/memory/orchestration/skill/vision`
均已位于 `src/bot/package/`。旧顶层包、`src/bot/core/` 目录与
`bot.handler` shim 已删除（Phase 5）；测试与源码统一从 `bot.package` 导入，
`tests/test_architecture.py` 守护旧路径不得再次出现。

---

## 1. 现状问题

1. `src/common/` 混合了四类职责：配置（`config.py`）、数据常量（`prompts.py` /
   `constants.py`）、基础设施（`database.py` / `logging.py` / `paths.py` /
   `queue.py` / `retry.py`）和 MCP 配置加载（`mcp.py`）。
2. Satori 代码被拆成 `domain/satori/*`（协议模型）与 `protocol/http|websocket`
   （传输客户端）两处，协议边界不清晰。
3. `src/context/` 实际只有 `utils/`，命名与内容不匹配。
4. `bot/core/` 同时承担了装配、事件流水线与 Satori 接入，`main.py` 里有大量手工
   装配代码（约 150 行），`app` 与 `boot` 没有显式概念。
5. `orchestration.graph.create_graph` 反向依赖 `execution.tools.build_tools`：
   编排层自己装配工具，造成 `orchestration -> tools` 的依赖倒置，也让测试和复用
   必须同时拉起工具工厂。

---

## 2. 目标目录结构

```text
src/
├── bot/
│   ├── __init__.py                   # 轻量包门面；禁止重导出重型子包，防循环导入
│   └── package/                      # 应用包主体（package root）
│       ├── __init__.py
│       ├── config/                   # 配置类
│       │   ├── __init__.py
│       │   └── settings.py           # BotConfig / Flag / 解析器（原 common/config.py）
│       │
│       ├── core/                     # 应用核心：装配与生命周期
│       │   ├── __init__.py
│       │   ├── app.py                # BotApplication / Runtime：组装结果 + run/start/stop
│       │   ├── boot.py               # create_app / load_config / init_infrastructure
│       │   ├── database.py           # DatabaseManager（原 common/database.py）
│       │   └── llm.py                # setup_llm（原 bot/core/llm.py）
│       │
│       ├── pipeline/                 # 事件流水线处理（协议无关）
│       │   ├── __init__.py
│       │   ├── contracts.py          # MessageRouter / MessageSink / Compactor 端口
│       │   ├── router.py             # route_incoming（原 bot/core/router.py）
│       │   ├── dispatcher.py         # MessageDispatcher（原 bot/core/dispatcher.py）
│       │   ├── worker.py             # MessageWorkerPool（原 bot/core/worker.py）
│       │   └── pipeline.py           # MessagePipeline：队列/去重/worker 生命周期门面
│       │
│       ├── utils/                    # 纯工具/横切设施
│       │   ├── __init__.py
│       │   ├── content_parser.py     # 原 context/utils/content_parser.py
│       │   ├── context.py            # 原 context/utils/context.py
│       │   ├── messages.py           # 原 context/utils/messages.py
│       │   ├── reply_policy.py       # 原 context/utils/reply_policy.py
│       │   ├── routing.py            # 原 context/utils/routing.py
│       │   ├── logging.py            # 原 common/logging.py（trace_context/setup_logging）
│       │   ├── paths.py              # 原 common/paths.py（PROJECT_ROOT）
│       │   ├── queue.py              # 原 common/queue.py（InMemoryMessageQueue）
│       │   └── retry.py              # 原 common/retry.py（retry_async）
│       │
│       ├── platform/                 # 平台适配层；目前只有 Satori
│       │   ├── __init__.py           # platform registry
│       │   ├── base.py               # EventSource / PlatformAdapter / MessageSender 端口
│       │   └── satori/
│       │       ├── __init__.py
│       │       ├── enums.py          # 原 domain/satori/enums.py
│       │       ├── models.py         # 原 domain/satori/models.py
│       │       ├── events.py         # 原 domain/satori/events.py
│       │       ├── api.py            # 原 domain/satori/api.py
│       │       ├── ingress.py        # SatoriMessageIngress（原 bot/core/ingress.py）
│       │       ├── adapter.py        # SatoriAdapter：事件注册 + 身份维护 + 生命周期
│       │       ├── http.py           # SatoriApiClient（原 protocol/http/client.py）
│       │       └── websocket.py      # SatoriClient（原 protocol/websocket/client.py）
│       │
│       ├── tools/                    # 工具类
│       │   ├── __init__.py
│       │   ├── factory.py            # build_tools（原 execution/tools/factory.py）
│       │   └── builtin/
│       │       ├── __init__.py
│       │       ├── run_bash.py       # 原 execution/tools/run_bash.py
│       │       ├── search_chat_history.py
│       │       ├── search_documents.py
│       │       ├── send_file.py
│       │       └── user_memory.py
│       │
│       ├── commands/                  # 图外斜杠命令上下文
│       ├── conversation/              # 会话领域对象
│       ├── domain/                    # 共享领域对象与端口
│       │   ├── __init__.py
│       │   ├── bash.py
│       │   ├── constants.py           # EXTERNAL_UPDATE_NODE / DIRECT_CHANNEL_TYPE
│       │   ├── media.py
│       │   ├── ports.py
│       │   ├── prompts.py             # 原 common/prompts.py
│       │   └── tasks.py
│       ├── knowledge/                 # RAG / 文档知识上下文
│       ├── memory/                    # 用户长期记忆上下文
│       ├── orchestration/             # LangGraph 编排（工具由 boot 注入）
│       ├── skill/                     # 技能上下文
│       ├── vision/                    # 视觉理解上下文
│       └── mcp/                       # MCP 类
│           ├── __init__.py
│           ├── config.py              # load_mcp_servers_from_file（原 common/mcp.py）
│           └── client.py              # load_mcp_tools（原 execution/mcp/client.py）
│
main.py                               # 只保留薄入口：create_app() -> app.run()
```

> `src/common/`、`src/context/`、`src/execution/`、`src/protocol/`、
> `src/commands/`、`src/conversation/`、`src/domain/`、`src/knowledge/`、
> `src/memory/`、`src/orchestration/`、`src/skill/`、`src/vision/`
> 这些旧顶层路径已删除；新代码统一使用 `src/bot/package/` 路径。

---

## 3. 文件迁移表

### 3.1 `common` 拆分

| 原文件 | 目标文件 | 分类 |
|---|---|---|
| `src/common/config.py` | `src/bot/package/config/settings.py` | 配置类 |
| `src/common/prompts.py` | `src/bot/package/domain/prompts.py` | 数据对象 |
| `src/common/constants.py` | `src/bot/package/domain/constants.py` | 数据对象 |
| `src/common/database.py` | `src/bot/package/core/database.py` | 基础设施/装配 |
| `src/common/logging.py` | `src/bot/package/utils/logging.py` | 工具 |
| `src/common/paths.py` | `src/bot/package/utils/paths.py` | 工具 |
| `src/common/queue.py` | `src/bot/package/utils/queue.py` | 工具/适配器 |
| `src/common/retry.py` | `src/bot/package/utils/retry.py` | 工具 |
| `src/common/mcp.py` | `src/bot/package/mcp/config.py` | MCP 配置加载 |

`BotConfig` 只留在 `bot.package.config.settings`；提示词、图节点常量属于“数据对象”，
放入 `src/bot/package/domain/`，不再新建 `entity/`。如果后续数据对象变多，
可再引入 `src/entity/`，但当前项目规模下 `domain/` 已足够。

### 3.2 `context/utils` 拆分

| 原文件 | 目标文件 |
|---|---|
| `src/context/utils/content_parser.py` | `src/bot/package/utils/content_parser.py` |
| `src/context/utils/context.py` | `src/bot/package/utils/context.py` |
| `src/context/utils/messages.py` | `src/bot/package/utils/messages.py` |
| `src/context/utils/reply_policy.py` | `src/bot/package/utils/reply_policy.py` |
| `src/context/utils/routing.py` | `src/bot/package/utils/routing.py` |

`src/context/__init__.py` 与 `src/context/utils/__init__.py` 删除，导出职责上收到
`src/bot/package/utils/__init__.py`。

### 3.3 Satori 平台收敛

| 原文件 | 目标文件 |
|---|---|
| `src/domain/satori/enums.py` | `src/bot/package/platform/satori/enums.py` |
| `src/domain/satori/models.py` | `src/bot/package/platform/satori/models.py` |
| `src/domain/satori/events.py` | `src/bot/package/platform/satori/events.py` |
| `src/domain/satori/api.py` | `src/bot/package/platform/satori/api.py` |
| `src/protocol/websocket/client.py` | `src/bot/package/platform/satori/websocket.py` |
| `src/protocol/http/client.py` | `src/bot/package/platform/satori/http.py` |
| `src/bot/package/core/ingress.py` | `src/bot/package/platform/satori/ingress.py` |

`domain/__init__.py` 的 lazy-loading 映射要同步删除所有 Satori 名称；Satori 导出
改由 `bot.package.platform.satori.__init__` 提供。

### 3.4 流水线拆分

| 原文件 | 目标文件 | 说明 |
|---|---|---|
| `src/bot/package/core/router.py` | `src/bot/package/pipeline/router.py` | 协议无关路由 |
| `src/bot/package/core/dispatcher.py` | `src/bot/package/pipeline/dispatcher.py` | 路由决策执行 |
| `src/bot/package/core/worker.py` | `src/bot/package/pipeline/worker.py` | 队列 + 并发 + burst 合并 |
| `src/bot/handler.py` | `src/bot/package/pipeline/pipeline.py` | 改为 `MessagePipeline` |
| `src/bot/package/core/llm.py` | `src/bot/package/core/llm.py` | 保留 |
| 无 | `src/bot/package/core/app.py` | 新增 |
| 无 | `src/bot/package/core/boot.py` | 新增 |

### 3.5 上下文包整体下移

所有限界上下文从 `src/<context>/` 平铺，统一移动到 `src/bot/package/<context>/`：

| 原包 | 目标包 |
|---|---|
| `src/commands/` | `src/bot/package/commands/` |
| `src/conversation/` | `src/bot/package/conversation/` |
| `src/domain/` | `src/bot/package/domain/` |
| `src/knowledge/` | `src/bot/package/knowledge/` |
| `src/memory/` | `src/bot/package/memory/` |
| `src/orchestration/` | `src/bot/package/orchestration/` |
| `src/skill/` | `src/bot/package/skill/` |
| `src/vision/` | `src/bot/package/vision/` |

旧顶层包已删除；新代码统一从 `bot.package.<context>` 导入。

### 3.6 工具/MCP 拆分

| 原文件 | 目标文件 |
|---|---|
| `src/execution/tools/factory.py` | `src/bot/package/tools/factory.py` |
| `src/execution/tools/run_bash.py` | `src/bot/package/tools/builtin/run_bash.py` |
| `src/execution/tools/search_chat_history.py` | `src/bot/package/tools/builtin/search_chat_history.py` |
| `src/execution/tools/search_documents.py` | `src/bot/package/tools/builtin/search_documents.py` |
| `src/execution/tools/send_file.py` | `src/bot/package/tools/builtin/send_file.py` |
| `src/execution/tools/user_memory.py` | `src/bot/package/tools/builtin/user_memory.py` |
| `src/execution/mcp/client.py` | `src/bot/package/mcp/client.py` |

---

## 4. 各包职责

### 4.1 `bot.package.config`

只放配置类与配置解析器：

```python
# src/bot/package/config/__init__.py
from .settings import BotConfig, Flag

__all__ = ["BotConfig", "Flag"]
```

- `BotConfig` 继续使用 pydantic-settings，从 `.env` 读取全部运行参数。
- `_parse_flag` / `_parse_comma_list` / `Flag` 属于配置解析细节，不放 `utils`。
- `settings.py` 不 import `bot.package.core`、`orchestration` 等任何上层模块。
- `find_dotenv()` 逻辑保持现状：解析项目根 `.env` 绝对路径，避免 CWD 差异。

### 4.2 `bot.package.core`：app.py 与 boot.py

`boot.py` 只负责“造出来”，`app.py` 只负责“跑起来和关掉”。

```python
# src/bot/package/core/app.py（示意）
class BotApplication:
    """装配完成的 bot 运行时。"""

    def __init__(self, deps: AppDependencies) -> None:
        self.config = deps.config
        self.platform = deps.platform            # SatoriAdapter
        self.pipeline = deps.pipeline            # MessagePipeline
        self.graph = deps.graph
        self.checkpointer = deps.checkpointer
        self.index_worker = deps.index_worker
        self.command_registry = deps.command_registry
        self.command_services = deps.command_services
        self.rag_service = deps.rag_service
        self.document_store = deps.document_store
        self.memory_store = deps.memory_store
        self.vision_service = deps.vision_service

    async def start(self) -> None:
        await self.pipeline.start()
        if self.index_worker is not None:
            await self.index_worker.start()
        self.platform.bind_pipeline(self.pipeline)
        self.platform.register_handlers()

    async def run(self) -> None:
        await self.platform.run()

    async def stop(self) -> None:
        await self.pipeline.stop()
        if self.index_worker is not None:
            await self.index_worker.stop()
        await self.platform.close()
        await self.memory_store.close()
        ...
```

```python
# src/bot/package/core/boot.py（示意）
async def create_app(config: BotConfig | None = None) -> BotApplication:
    config = config or load_config()
    setup_logging("log")
    db = DatabaseManager(config.db_dir)
    db.ensure_ready()

    llm = setup_llm(config)
    rag_service = await _init_rag(config)
    document_store = _init_document_store(config, rag_service)
    memory_store = MemoryStore(config.db_dir)
    vision_service = _init_vision(config)
    mcp_tools = await load_mcp_tools(
        load_mcp_servers_from_file(config.mcp_servers_file, env=load_env_values())
    )
    skill_registry = _init_skills(config)

    tools = build_tools(
        rag_service=rag_service,
        document_store=document_store,
        memory_store=memory_store,
        mcp_tools=mcp_tools,
        skill_registry=skill_registry,
        bash_config=BashConfig(...),
        file_sender=api_client,
        send_roots=[...],
    )
    graph, checkpointer = await create_graph(
        llm, config, db_dir=config.db_dir, tools=tools,
        rag_service=rag_service, document_store=document_store,
        memory_store=memory_store, vision_service=vision_service,
        skill_registry=skill_registry,
    )

    pipeline = build_pipeline(...)
    platform = build_satori_platform(config, pipeline)
    return BotApplication(...)
```

改造后 `main.py` 只保留：

```python
from bot.package.core.boot import create_app

async def main():
    app = await create_app()
    try:
        await app.start()
        await app.run()
    except KeyboardInterrupt:
        pass
    finally:
        await app.stop()
```

`boot.py` 中每个可降级组件继续沿用“初始化失败只降级、不阻断启动”的现有策略：
RAG / DocumentStore / Vision / MCP / Skill 都允许为 `None` 或空列表。

### 4.3 `bot.package.pipeline`

`pipeline` 只处理**已经归一化的 `IncomingMessage`**，不 import 任何
Satori 模型。Satori 的 `EventBody -> IncomingMessage` 转换在
`platform/satori/ingress.py` 完成。

```text
platform.satori.websocket
        │  EventBody
        ▼
platform.satori.ingress
        │  IncomingMessage | None
        ▼
pipeline.pipeline.MessagePipeline
        │  dedup(event_id) -> asyncio.Queue
        ▼
pipeline.worker.MessageWorkerPool
        │  per-thread lock + burst batch
        ▼
pipeline.router.route_incoming
        │  RouteDecision
        ▼
pipeline.dispatcher.MessageDispatcher
        ├── COMMAND      -> commands.CommandRegistry/CommandServices
        ├── REPLY        -> ContextCompactor -> graph.ainvoke -> send -> IndexWorker
        ├── CONTEXT_ONLY -> graph.aupdate_state -> IndexWorker
        └── SYSTEM/MEDIA/IGNORE
```

新增 `contracts.py`，把 worker/dispatcher 对具体服务的依赖改成端口注入：

```python
class MessageRouter(Protocol):
    def __call__(self, message: IncomingMessage, **opts) -> RouteDecision: ...

class MessageSink(Protocol):
    async def dispatch(self, message: IncomingMessage, decision: RouteDecision,
                       *, auto_reply_allowed: bool = False) -> None: ...

class ContextCompactorPort(Protocol):
    async def compact_if_needed(self, thread_id: str) -> int: ...
```

`MessagePipeline` 负责持有 `queue_factory`、`dedup_size`、`worker_count` 和
worker 生命周期；`platform` 和 `core` 不再直接操作 worker 内部状态。

### 4.4 `bot.package.utils`

`utils` 只放纯函数或轻量基础设施，禁止反向依赖 `pipeline/platform/tools/core`：

- `content_parser.py`：Satori 消息 XML 解析，输出 `ParsedContent`。
- `context.py`：system 层构建、token 估算、多模态 content 归一化。
- `messages.py`：日志格式化与发言者解析。
- `reply_policy.py`：auto_reply 随机/冷却判定。
- `routing.py`：回复/入上下文判定表。
- `logging.py`：`setup_logging` + `trace_context`。
- `paths.py`：`PROJECT_ROOT`。
- `queue.py`：`InMemoryMessageQueue`。
- `retry.py`：`retry_async`。

注意事项：

1. `bot.package.utils.routing` 当前依赖 `domain.satori.ChannelType`。Satori 模型迁入
   `platform` 后，不要让 `utils -> platform`。建议在 `domain/constants.py` 中
   增加 `DIRECT_CHANNEL_TYPE = 1`，routing 只比较整型；或者把 `ChannelType`
   下沉为 `domain/channel.py` 共享枚举。
2. `bot.package.utils.context` 的 `SKILL_ACTIVE_HINT` / `SKILL_INDEX_HINT` 改从
   `domain.prompts` 导入。
3. `bot.package.utils.__init__` 保持显式 `__all__`，不要 `import *` 拉起重依赖。

### 4.5 `bot.package.platform`

```text
platform/
├── base.py        # EventSource, PlatformAdapter, MessageSender 端口
└── satori/        # 具体平台实现
```

`base.py` 建议定义：

```python
class EventSource(Protocol):
    def on(self, event_type: str):
        """注册事件回调。"""

    async def run(self) -> None: ...
    async def close(self) -> None: ...

class PlatformAdapter(Protocol):
    def bind_pipeline(self, pipeline: MessagePipeline) -> None: ...
    def register_handlers(self) -> None: ...
    async def run(self) -> None: ...
    async def close(self) -> None: ...
```

Satori 包职责：

| 文件 | 职责 |
|---|---|
| `enums.py` | `ChannelType` / `LoginStatus` / `Direction` / `Order` |
| `models.py` | Satori API 资源模型：`User/Guild/Channel/Message/...` |
| `events.py` | `Signal` / `EventBody` / `LoginList` |
| `api.py` | Satori Endpoint 常量与请求参数模型 |
| `ingress.py` | `EventBody` 校验并归一化为 `IncomingMessage` |
| `http.py` | `SatoriApiClient`：`send_message` / `send_file` / `call_api` |
| `websocket.py` | `SatoriClient`：连接、心跳、事件分发、重连 |

`platform/__init__.py` 只提供注册表，不默认 import Satori，保证未来接入
OneBot、QQ 官方 WebSocket 时不需要改动 core/pipeline。

### 4.6 `bot.package.tools`

```text
tools/
├── factory.py      # 依赖注入 + BaseTool 装配
├── registry.py     # 可选：工具启停/统计
└── builtin/        # 每个内部工具一个纯函数文件
```

约束：

- `builtin/*` 保持纯函数或单工具实现，不 import LangGraph 图节点。
- `factory.py` 是唯一把服务依赖闭包进 `StructuredTool` 的地方。
- `create_graph` **不再调用** `build_tools`；`boot.py` 装配好
  `list[BaseTool]` 后注入图。这是本次架构优化的关键反转。

### 4.7 `bot.package.mcp`

```text
mcp/
├── config.py   # 纯 stdlib：读取 JSON + ${ENV_VAR} 插值，不读 os.environ
└── client.py   # langchain-mcp-adapters 适配，逐 server 降级
```

- `config.py` 保持“调用方传入 env mapping”的项目约定，便于测试与密钥审计。
- `client.py` 继续只记录异常类名，不记录 repr/traceback，避免 URL 中 API key 泄漏。
- `bot.package.mcp.__init__` 不要 import `langchain_mcp_adapters`，惰性导出
  `client.py` 即可。

---

## 5. 依赖分层（2026-08-20 更新：domain 纯化、端口归一、BotState 瘦身已完成）

> **2026-08-20 变更**：`domain/bash` → `tools/domain.BashConfig`、`domain/prompts` → `orchestration/prompts + knowledge/prompts + vision/prompts`、`domain/constants` → `orchestration/constants + platform/satori/constants`、`pipeline/contracts` → `domain/ports`、`conversation/state` → `conversation/turn.TurnInput`。`scripts/check_package_dependencies.py` 已升级为子包粒度，`bot.package.mcp` 合并，`platform`/`utils` 循环已通过 TYPE_CHECKING/内联打破。详见 `docs/superpowers/plans/2026-08-20-context-packaging-refactor.md`。

## 5. 依赖分层

目标分层（越往下越稳定）：

```text
L5  bot.package.core             # 装配根：app.py / boot.py
        │
L4  bot.package.pipeline  bot.package.tools  bot.package.mcp.client  bot.package.platform
        │
L3  bot.package.commands  bot.package.knowledge  bot.package.memory
        │  bot.package.orchestration  bot.package.skill  bot.package.vision
        │
L2  bot.package.config  bot.package.utils  bot.package.mcp.config
        │
L1  bot.package.domain  bot.package.conversation
        │
L0  stdlib / 第三方库
```

依赖规则：

| 包 | 允许依赖 |
|---|---|
| `bot.package.domain` | 无内部依赖（BashConfig 已迁移至 tools/domain，prompts/constants 已拆分，保留垫片期 duplicate 避免循环） |
| `bot.package.conversation` | `bot.package.domain`（新增 `turn.py: TurnInput` 当轮输入，与 BotState 持久态分离） |
| `bot.package.config` | 无内部依赖（DEFAULT_PERSONA_PROMPT 内联，不再依赖 domain/orchestration） |
| `bot.package.utils` | `bot.package.domain`、`bot.package.conversation`、`bot.package.orchestration`（context 需要 SKILL 提示词） |
| `bot.package.mcp` | `bot.package.config`、`bot.package.utils`（mcp.config + client 合并，共享 PROJECT_ROOT） |
| `bot.package.commands` | `bot.package.config`、`bot.package.utils`、`bot.package.domain`、`bot.package.conversation`、`bot.package.orchestration`（clear/compact 需 EXTERNAL_UPDATE_NODE） |
| `bot.package.skill` | 无内部依赖 |
| `bot.package.knowledge` | `bot.package.config`、`bot.package.utils`、`bot.package.domain`（新增 `prompts.py: RETRIEVAL_TASK`） |
| `bot.package.memory` | 无内部依赖 |
| `bot.package.vision` | `bot.package.config`、`bot.package.utils`、`bot.package.domain`（新增 `prompts.py: VISION_PROMPT`） |
| `bot.package.orchestration` | `bot.package.config`、`bot.package.utils`、`bot.package.domain`、`bot.package.conversation`、`bot.package.vision`、`bot.package.tools`（graph 需 BashConfig，describe_image 需 Vision） |
| `bot.package.platform` | `bot.package.config`、`bot.package.utils`、`bot.package.domain`、`bot.package.conversation`（已改为 TYPE_CHECKING 避免循环） |
| `bot.package.tools` | `bot.package.config`、`bot.package.utils`、`bot.package.domain`、`bot.package.conversation`、`bot.package.skill`、`bot.package.knowledge`（新增 `domain.py: BashConfig`） |
| `bot.package.pipeline` | `bot.package.config`、`bot.package.utils`、`bot.package.domain`、`bot.package.conversation`、`bot.package.commands`、`bot.package.orchestration`（contracts 已收敛至 domain.ports） |
| `bot.package.core` | 以上全部 |

两条硬性不变量：

1. `bot/__init__.py` 必须保持轻量：**不得** import `bot.package.orchestration/knowledge/vision`
   等会回依赖 `bot.package.utils` 的重型包，否则会形成
   `bot.package -> bot.package.orchestration -> bot.package.utils -> bot.package.__init__` 循环。
2. `bot.package.orchestration` **不得** import `bot.package.tools`。工具列表由 `bot.package.core.boot`
   装配后传入 `create_graph(..., tools=tools)`。

`scripts/check_package_dependencies.py` 需要从“顶层包粒度”升级为
“子包粒度”的白名单，至少把 `bot.package.config`、`bot.package.utils`、`bot.package.mcp.config`
标记为 shared kernel，允许 feature 包依赖。

---

## 6. 关键接口调整

### 6.1 `create_graph` 改为依赖注入

```python
async def create_graph(
    llm: ChatOpenAI,
    config: BotConfig,
    *,
    tools: list[BaseTool],
    db_dir: str = "db",
    rag_service=None,
    document_store=None,
    memory_store=None,
    vision_service=None,
    skill_registry=None,
    file_sender=None,
) -> tuple[CompiledStateGraph, AsyncSqliteSaver]:
    ...
    builder.add_node("tools", ToolNode(tools, handle_tool_errors=_tool_error_message))
    ...
```

`boot.py` 组装 tools，`create_graph` 只消费 tools。这样测试图时可以传入 fake
tools，不必构造 RAG/Memory/MCP 全链路。

### 6.2 `MessagePipeline` 替代 `MessageHandler`

当前 `MessageHandler` 是“协议适配门面 + 队列装配”的混合体。目标改为：

```python
pipeline = MessagePipeline(
    dispatcher=dispatcher,
    router=route_incoming,
    bot_config=config,
    command_registry=command_registry,
    identity=identity,
    worker_count=config.message_worker_count,
    queue_maxsize=config.message_queue_maxsize,
    batch_max=config.message_batch_max,
    dedup_size=config.message_dedup_size,
)
await pipeline.start()
await pipeline.enqueue(message)
await pipeline.stop()
```

`platform/satori` 只负责注册 `client.on("message-created")` 并把
`ingress.normalize(event)` 的结果交给 `pipeline.enqueue`。

### 6.3 `SatoriAdapter`

```python
class SatoriAdapter:
    def __init__(self, config, ws_client, api_client, pipeline): ...
    def bind_pipeline(self, pipeline): ...
    def register_handlers(self):
        self.ws.on("message-created")(self._on_message)
        self.ws.on("login")(self._on_login)

    async def _on_message(self, event: EventBody):
        message = self.ingress.normalize(event)
        if message is not None:
            await self.pipeline.enqueue(message)
```

---

## 7. 数据对象归属

目标原则：**配置类归 `bot.package.config`；跨上下文数据对象归 `domain`；纯工具归
`bot.package.utils`；某上下文专属对象留在该上下文包。**

| 对象 | 归属 |
|---|---|
| `BotConfig` | `bot.package.config.settings` |
| `Flag` / `_parse_flag` | `bot.package.config.settings` |
| `DEFAULT_PERSONA_PROMPT` 等提示词 | `domain/prompts.py` |
| `EXTERNAL_UPDATE_NODE` | `domain/constants.py` |
| `BashConfig` / `ImageDescription` / `IndexTurnTask` | `domain/`（保持不变） |
| `BotIdentity` / `IncomingMessage` / `RouteDecision` / `BotState` | `conversation/`（保持不变） |
| `MessageKind` / `Attachment` / `ParsedContent` | `conversation/content.py`（保持不变） |
| `DatabaseManager` | `bot.package.core.database` |
| `InMemoryMessageQueue` | `bot.package.utils.queue` |
| `retry_async` | `bot.package.utils.retry` |
| `trace_context` / `setup_logging` | `bot.package.utils.logging` |

`src/common/__init__.py` 当前的聚合导出删除；各调用方改成从明确包导入，避免
再次出现“公共杂物间”。

---

## 8. 运行流程（重构后）

```text
main.py
  └─ bot.package.core.boot.create_app()
       ├─ load_config() -> BotConfig
       ├─ setup_logging()
       ├─ DatabaseManager.ensure_ready()
       ├─ 初始化 RAG / DocumentStore / Memory / Vision / MCP / Skill
       ├─ bot.package.tools.build_tools(...)
       ├─ orchestration.create_graph(tools=...)
       ├─ pipeline 装配 MessagePipeline(...)
       └─ platform 装配 SatoriAdapter(...)
  └─ BotApplication.start()
       ├─ pipeline.start()
       ├─ index_worker.start()
       └─ platform.register_handlers()
  └─ BotApplication.run()
       └─ SatoriClient.run()（WS 收事件 -> ingress -> pipeline）
  └─ BotApplication.stop()
       ├─ pipeline.stop() -> index_worker.stop() -> platform.close()
       └─ rag/document/vision/memory/db close
```

---

## 9. 分阶段迁移计划

### Phase 0：基线

```bash
uv run python -m pytest
uv run ruff check
uv run python scripts/check_package_dependencies.py
```

### Phase 1：迁移纯函数与共享数据对象

1. 新建 `bot.package.utils/`，搬入 `context/utils/*` 与 `common/{logging,paths,queue,retry}.py`。
2. 新建 `bot.package.config.settings`。
3. 新建 `domain/prompts.py`、`domain/constants.py`。
4. 保留 `context/`、`common/` 为 re-export shim（打 `DeprecationWarning`）。
5. 更新 `bot.package.knowledge/vision/orchestration/commands` 的 import。
6. 跑测试 + ruff + 依赖检查。

### Phase 2：迁移 Satori 平台

1. 新建 `bot.package.platform/base.py` 与 `bot.package.platform/satori/`。
2. 搬入 `domain/satori/*`、`protocol/*`、`bot/core/ingress.py`。
3. 更新 `domain/__init__.py` 删除 Satori lazy 映射。
4. 更新 routing 对 `ChannelType` 的依赖（使用共享常量或 domain 枚举）。
5. 保留 `protocol/`、`domain.satori` shim。
6. 跑 `test_satori_*`、`test_routing`、`test_reply_policy`。

### Phase 3：迁移 tools/mcp，拆出 pipeline

1. 新建 `bot.package.tools/`、`bot.package.mcp/`。
2. 新建 `bot.package.pipeline/{router,dispatcher,worker,pipeline,contracts}.py`。
3. `create_graph` 增加 `tools` 注入参数，移除 `execution.tools` 依赖。
4. `MessageHandler` 逻辑拆成 `MessagePipeline` + `SatoriAdapter`。
5. 保留 `execution/` 与 `bot.core.{router,dispatcher,worker,ingress}` shim。
6. 跑消息流水线相关测试。

### Phase 4：新增 app/boot，瘦身 main

1. 新建 `bot.package.core/app.py`、`bot.package.core/boot.py`。
2. 把 `main.py` 的装配逻辑完整搬入 `boot.create_app`。
3. `main.py` 改为 15 行以内的薄入口。
4. 补充 `BotApplication.start/stop` 幂等性测试。

### Phase 5：删除旧包与 shim，更新工程质量设施

1. 删除 `src/common/`、`src/context/`、`src/execution/`、`src/protocol/`、
   `src/commands/`、`src/conversation/`、`src/domain/`、`src/knowledge/`、
   `src/memory/`、`src/orchestration/`、`src/skill/`、`src/vision/`，以及
   `bot/core/` 下旧 pipeline 文件。
2. 更新 `scripts/check_package_dependencies.py` 为子包粒度白名单。
3. 更新 `tests/test_architecture.py`：断言旧顶层包与旧模块路径不存在
   （`common`、`context`、`execution`、`protocol`、`commands`、`conversation`、
   `domain`、`knowledge`、`memory`、`orchestration`、`skill`、`vision`、
   `bot.core.router`、`bot.core.dispatcher`、`bot.core.worker`、
   `bot.core.ingress`、`bot.handler`）。
4. 更新 README 架构图与 AGENTS.md。
5. 全量回归。

---

## 10. 测试与验收标准

迁移完成必须满足：

- [ ] `uv run python -m pytest` 全绿。
- [ ] `uv run ruff check` 无新增告警。
- [ ] 新依赖检查脚本通过，且能识别 `bot.package.utils -> bot.package.platform`、
      `bot.package.orchestration -> bot.package.tools` 这类违规。
- [ ] `from bot.package.config import BotConfig` 可用。
- [ ] `from bot.package.utils import parse_content, estimate_context_tokens` 可用。
- [ ] `from bot.package.platform.satori import EventBody, SatoriClient, SatoriApiClient` 可用。
- [ ] `from bot.package.pipeline import MessagePipeline, route_incoming, MessageDispatcher` 可用。
- [ ] `from bot.package.tools import build_tools` 可用。
- [ ] `from bot.package.mcp import load_mcp_servers_from_file, load_mcp_tools` 可用。
- [ ] `from bot.package.core.boot import create_app` 可用，且 `main.py` 只调用 `create_app`。
- [ ] `bot/__init__.py` import 时间显著低于原先的 `bot/__init__.py`（不拉起
      langchain / milvus / aiosqlite 等重型依赖）。
- [ ] 删除旧包后无 `src/common`、`src/context`、`src/execution`、`src/protocol` 引用。

---

## 11. 风险与建议

1. **导入环风险**：`bot/__init__.py` 必须轻量。建议 CI 增加一个
   “`python -X importtime -c 'import bot'` 不导入 langgraph/milvus”的守护测试。
2. **老 checkpoint 兼容**：`ImageDescription` 的 serde allowlist 仍要保留
   `("domain.media", "ImageDescription")` 与 `("vision.domain", "ImageDescription")`。
   Satori 模型不是 checkpoint 内容，移动不影响历史数据。
3. **分阶段执行**：不要一次性移动并删除旧路径。每个 Phase 都用 shim 保持旧导入
   可用，测试全绿后再切下一个 Phase。
4. **依赖检查粒度**：同属 `bot` 的子包互导会在顶层包检查中“隐身”，必须升级
   检查脚本为子包粒度，否则重构后容易重新长出坏依赖。
5. **`entity/` 暂不引入**：当前共享数据对象数量少，先全部进 `domain/`；当出现
   与领域无关、又不是配置的纯传输对象时，再创建 `src/entity/` 并保持零依赖。
