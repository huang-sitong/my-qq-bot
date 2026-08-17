# qq-bot

基于 Satori 协议的 QQ 聊天机器人：LangGraph 驱动的多轮对话，支持同会话突发消息批量合并、群聊历史 RAG 检索（milvus-lite）、用户持久记忆、图片视觉理解（OpenAI 兼容视觉 / 多模态主 LLM）、MCP 外部工具、Markdown 技能与图外斜杠命令。

## 快速开始

```bash
uv sync                       # 安装依赖
cp .env-template .env         # 填写 BASE_URL / API_KEY 等配置
uv run python main.py         # 启动 bot
```

## 配置

所有运行参数统一由 `src/common/config.py` 的 `BotConfig`（pydantic-settings）从 `.env` 读取，完整环境变量清单见 `.env-template`。核心项：

| 变量 | 说明 |
|---|---|
| `BASE_URL` / `API_KEY` | 主 LLM OpenAI 兼容端点 |
| `BOT_LLM_MODEL` | 主 LLM 模型名（默认 `deepseek-v4-flash`） |
| `BOT_LLM_MULTIMODAL` | `1` 时图片直接进主 LLM；`0` 走视觉描述服务（`BOT_VISION_MODEL`，默认 `qwen3-vl:2b`） |
| `BOT_MESSAGE_WORKER_COUNT` | 消息 worker 数；不同 thread 可并发，同一 thread 仍串行（默认 `1`） |
| `BOT_MESSAGE_QUEUE_MAXSIZE` | 消息队列上限；`0` 无界，正整数满时入队阻塞形成背压 |
| `BOT_MESSAGE_BATCH_MAX` | 同会话突发消息合并上限；一次图调用/一条回复处理多条（默认 `4`，`0/1` 关闭） |
| `BOT_MESSAGE_DEDUP_SIZE` | event_id 幂等去重窗口；`0` 关闭（默认 `10000`） |
| `BOT_GRAPH_RECURSION_LIMIT` | LangGraph 图节点执行上限，工具回环会消耗该额度（默认 `128`） |
| `BOT_RAG_ENABLED` | 群聊历史向量检索（默认开启；嵌入用 OpenAI 兼容 Embedding API） |
| `BOT_EMBED_BASE_URL` | 嵌入专用 OpenAI 兼容地址；未设置时回落 `BASE_URL` |
| `BOT_EMBED_API_KEY` | 嵌入专用 API key；未设置时回落 `API_KEY` |
| `BOT_VISION_BASE_URL` | 视觉专用 OpenAI 兼容地址；未设置时回落 `BASE_URL` |
| `BOT_AUTO_REPLY` | 群聊非@消息自动回复总开关（默认关，可经 `/auto_reply` 运行时改） |
| `BOT_AUTO_REPLY_RANDOM_RATE` | auto_reply 非@消息的随机回复概率，默认 `0.3` |
| `BOT_AUTO_REPLY_COOLDOWN` | 同一会话两次 auto_reply 的最小间隔秒数，默认 `30` |
| `BOT_MCP_ENABLED` / `BOT_MCP_SERVERS_FILE` | MCP 外部工具（可选；server 定义在 `config/mcp_servers.json`） |
| `BOT_COMMAND_ENABLED` / `BOT_COMMAND_PREFIX` / `BOT_ADMIN_IDS` | 图外斜杠命令、前缀与管理员 ID |
| `BOT_SKILLS_ENABLED` / `BOT_SKILLS_DIR` | Markdown 技能模块（扫描 `skills/<name>/SKILL.md`） |
| `BOT_BASH_ENABLED` / `BOT_BASH_SHELL` | 技能脚本执行工具与 shell 路径（Windows Git Bash / WSL/Linux bash，默认 `bash`） |

## 运行时数据

`db/` 目录（`BOT_DB_DIR` 可改）启动时自动创建，库文件删除后重启会惰性重建。注意 `checkpoint.sqlite` 和 `memory.sqlite` 是真实数据，删除会丢会话状态与用户记忆：

- `checkpoint.sqlite` — LangGraph 会话状态
- `memory.sqlite` — 用户持久记忆（langgraph `AsyncSqliteStore`）
- `milvus.db` — 群聊历史向量（dense+sparse 混合检索）
- `embed_cache.sqlite` — 嵌入向量磁盘缓存

## 测试

```bash
uv run python -m pytest
```

## 架构

```text
Satori 事件 -> MessageHandler -> Ingress -> MessageWorkerPool
  -> Router -> Dispatcher
    - COMMAND       -> 图外命令 handler，不进图
    - REPLY         -> ContextCompactor -> LangGraph -> 发送回复 -> IndexWorker
    - CONTEXT_ONLY  -> graph.aupdate_state -> IndexWorker
    - SYSTEM/MEDIA/IGNORE -> 结束
```

同 thread 消息由 per-thread lock 串行；`BOT_MESSAGE_BATCH_MAX` 开启时，worker 在进入图前机会式合并连续突发消息，整批一次图调用、一条回复。批内命令仍按原位置单独执行，配置变更可作用于批内后续消息，RAG 索引按每条消息入队。

主要模块：

- `main.py` — 装配 config / LLM / graph / handler / compactor / IndexWorker / RagService / MemoryStore
- `src/protocol/` — Satori/OneBot 协议接入（WS/HTTP 收发）
- `src/orchestration/` — 会话编排：LangGraph 工作流组装与图节点
- `src/execution/` — 工具执行：内部工具 + MCP 工具加载
- `src/context/` — 上下文管理：消息解析、token 估算、回复判定等纯工具
- `src/bot/core/` — 消息流水线（ingress/router/dispatcher/worker/llm）
- `src/commands/` — 图外斜杠命令上下文
- `src/skill/` — 技能管理上下文
- `src/knowledge/` — 群聊历史 hybrid search 与后台索引（RAG）
- `src/memory/` — 用户长期记忆上下文
- `src/vision/` — 图片理解上下文
- `src/bot/core/worker.py` — 消息队列、per-thread 串行与批量合并
- `src/bot/core/router.py` — 确定性回复判定
- `src/bot/core/dispatcher.py` — 命令 / 上下文 / 回复图分发
- `src/orchestration/graph.py` — 最小 LangGraph 对话与工具回环
- `src/orchestration/compaction.py` — 图外上下文压缩
- `src/domain/` — Satori 协议数据对象与跨上下文共享 DTO（media/tasks/bash）
- 旧的 `bot.transport`、`bot.core.rag`、`bot.core.skills`、`bot.core.vision`、`bot.core.commands`、`domain.bot` 等兼容层已彻底移除

更完整的架构约定和开发细节见 `AGENTS.md`。
