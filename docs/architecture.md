# qq-bot 目标架构

本文档是 `src/bot/package/` 的架构契约。项目采用 **DDD-lite 模块化单体 +
端口-适配器**：按限界上下文分包，依赖方向由
`scripts/check_package_dependencies.py` 守护，领域层保持框架无关。

## 设计目标

1. 会话业务规则归会话领域，基础设施通过端口注入。
2. 领域层不 import LangChain / LangGraph / 持久化框架。
3. 仓库与外部服务只通过领域端口消费。
4. 会话状态修改由 `Conversation` 聚合根收口。
5. RAG 索引用领域事件解耦，消息主流程不感知索引细节。
6. `utils` 只保留日志、路径、队列、重试、事件总线等纯技术横切设施。

## 包结构

```text
src/bot/package/
  config/                 # BotConfig（pydantic-settings）
  core/                   # app 运行时容器 + boot 组合根 + database/llm
  conversation/           # 纯会话领域（无 LangChain/LangGraph）
    conversation.py       #   Conversation 聚合根
    policy.py             #   ReplyPolicy / ReplyDecision
    record.py             #   MessageRecord
    events.py             #   ConversationTurnCompleted
    content.py            #   MessageKind / Attachment / ParsedContent / IMAGE_PLACEHOLDER
    message.py            #   IncomingMessage（协议归一化后的领域输入）
    router.py             #   RouteAction / RouteDecision
    turn.py               #   TurnInput（当轮输入，不落库）
    identity.py           #   BotIdentity
  domain/                 # 共享内核：跨上下文 DTO、端口、仓库抽象、领域事件基类
    events.py             #   DomainEvent / DomainEventBus
    repositories.py       #   ConversationRepository / DocumentRepository / MemoryRepository
    ports.py              #   MessageQueue / MessageSender / MessageRouter / MessageSink / RagIndexer / VisionServicePort ...
    tasks.py              #   IndexTurnTask
    media.py              #   ImageDescription
  pipeline/               # 协议无关消息流水线：pipeline / worker / router / dispatcher
  platform/               # 平台适配层
    base.py               #   EventSource / PlatformAdapter
    satori/               #   Satori 协议实现 + content_parser
  knowledge/              # RAG / 文档知识上下文
    turn_index_projection.py  # 订阅会话领域事件，投影 IndexTurnTask
  memory/                 # 用户记忆适配器（MemoryRepository）
  orchestration/          # LangGraph 工作流编排
    state.py              #   BotState：框架状态投影
    conversation_repository.py  # LangGraphConversationRepository
    graph.py              #   图组装
    nodes/                #   llm_node / action_node
  commands/               # 图外斜杠命令上下文
  tools/                  # LLM 工具装配与纯函数
  mcp/                    # MCP 工具加载
  skill/                  # 技能上下文
  vision/                 # 视觉理解上下文
  utils/                  # 纯技术横切：context/messages/event_bus/logging/paths/queue/retry
```

## 依赖方向

`scripts/check_package_dependencies.py` 以子包粒度检查运行时 import，核心规则：

- `domain` 不依赖任何内部子包。
- `conversation` 只依赖 `domain`，且禁止 LangChain/LangGraph import。
- `orchestration` 不依赖 `tools`；工具由 `core.boot` 装配后注入。
- `knowledge` 允许依赖 `conversation`：RAG 索引投影订阅会话领域事件。
- `core` 是唯一组合根，负责具体适配器的创建与订阅装配。

运行检查：

```bash
uv run python scripts/check_package_dependencies.py
uv run ruff check src tests
uv run python -m pytest
```

## 关键数据流

```text
Satori 事件
  -> SatoriAdapter -> SatoriMessageIngress（协议 XML -> IncomingMessage）
  -> MessageWorkerPool（按 thread 串行，突发消息机会式合并）
  -> Router（Conversation.decide() -> RouteDecision）
  -> MessageDispatcher
       COMMAND       -> 图外命令
       REPLY         -> graph.ainvoke -> send_message
                        -> publish ConversationTurnCompleted
       CONTEXT_ONLY  -> ConversationRepository.append_record / graph.aupdate_state
                        -> publish ConversationTurnCompleted(bot_reply="")
  -> InMemoryDomainEventBus
  -> TurnIndexProjection -> IndexTurnTask -> IndexWorker -> RagService -> Milvus
```

## DDD 构件映射

| DDD 构件 | 位置 |
|---|---|
| 实体 / 聚合根 | `conversation/conversation.py::Conversation` |
| 值对象 | `MessageRecord`、`ReplyDecision`、`TurnInput`、`RouteDecision`、`ImageDescription` |
| 领域服务 | `conversation/policy.py::ReplyPolicy` |
| 领域事件 | `conversation/events.py::ConversationTurnCompleted` |
| 仓库端口 | `domain/repositories.py` |
| 仓库适配器 | `LangGraphConversationRepository`、`DocumentStore`、`MemoryStore` |
| 端口 / 适配器 | `domain/ports.py` + `platform/satori`、`utils/queue.py`、`utils/event_bus.py` |
| 组合根 | `core/boot.py::create_app` |

## 架构升级记录

1. 提取纯会话领域模型：`ReplyPolicy` / `ReplyDecision` / `MessageRecord` 迁入
   `conversation`，删除 `utils/routing.py`、`utils/reply_policy.py`。
2. 框架隔离：`BotState` 从会话领域迁至 `orchestration/state.py`，成为
   LangGraph 状态投影。
3. 仓库端口：新增 `ConversationRepository` / `DocumentRepository` /
   `MemoryRepository`，并实现对应适配器。
4. 聚合根：`Conversation` 统一管理 messages / summary / active_skills /
   tool_rounds 的修改，图节点与命令层经聚合方法更新状态。
5. 领域事件：`ConversationTurnCompleted` + `DomainEventBus` +
   `TurnIndexProjection`，dispatcher 不再直接拼装 RAG 索引任务。
6. utils 纯化：Satori content 解析迁入 `platform/satori`，
   `IMAGE_PLACEHOLDER` 迁入 `conversation/content.py`，`utils` 仅保留技术横切设施。
