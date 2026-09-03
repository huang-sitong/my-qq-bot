# qq-bot

基于 Satori 协议的 QQ 聊天机器人：LangGraph 驱动的多轮对话，支持同会话突发消息批量合并、群聊历史 RAG 检索（milvus-lite）、用户持久记忆、图片视觉理解（OpenAI 兼容视觉 / 多模态主 LLM）、MCP 外部工具、Markdown 技能与图外斜杠命令。

## 快速开始

```bash
uv sync                       # 安装依赖
cp .env-template .env         # 填写 BASE_URL / API_KEY 等配置
uv run python main.py         # 启动 bot
```

## 控制台后端（Web API）

`src/bot/package/api/` 提供 FastAPI 控制台后端，前端在 `web/`（Vue 3 + Vite + Element Plus）。启动：

```bash
uv run python console_api.py            # 默认 127.0.0.1:8000
uv run python console_api.py --port 9000
```

接口（已开 CORS 放行 Vite 默认的 `localhost:5173`）：

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/health` | 健康检查 |
| `GET` | `/api/settings` | 返回全部设置项（字段名、env 变量名、值、类型、分组、是否敏感） |
| `PUT` | `/api/settings` | 部分更新设置，`{"字段名": 新值}`；`null` 清除该项恢复默认 |
| `WS` | `/api/logs/ws` | 日志实时流：推送 bot 本次运行的完整日志（含打开前的历史，INFO 起，JSON 行） |

说明：

- 设置读写直接复用 `BotConfig` + `.env`，**不引入 YAML**，保持单一配置源；改动写回 `.env` 后按项目约定**重启生效**。
- 敏感字段（`token`/`llm_api_key`/`embed_api_key`/`vision_api_key`/`document_mineru_api_key`）在 GET 中以 `***` 掩码返回；PUT 原样回传 `***` 视为不修改。
- 未知字段返回 `400`，类型/校验失败返回 `422`，都不会写盘。
- 日志页在 `http://localhost:5173/logs`（Web 前端）。开发时 Vite 把 `/api`（含 WebSocket）代理到控制台后端，无需额外跨域配置。

## 服务启动脚本

项目根目录提供 3 个启动脚本（后台运行，日志在 `log/`，pid 在 `log/*.pid`）：

| 脚本 | 说明 |
|---|---|
| `./start_bot.sh` | 启动 QQ Bot（`main.py`） |
| `./start_console.sh` | 启动控制台：FastAPI 后端（`console_api.py`，端口 8000）+ Web 前端（Vite dev，端口 5173） |
| `./start_all.sh` | 启动全部：Bot + 控制台后端 + Web 前端 |

每个脚本均支持：

```bash
./start_bot.sh                  # 后台启动（start_console / start_all 同理）
./start_bot.sh --status         # 查看运行状态
./start_bot.sh --stop           # 停止（start_all --stop 停止全部）
./start_bot.sh --foreground        # 仅 start_bot 支持前台运行（Ctrl+C 停止）
```

## 配置

所有运行参数统一由 `src/bot/package/config/settings.py` 的 `BotConfig`（pydantic-settings）从 `.env` 读取，完整环境变量清单见 `.env-template`。核心项：

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
| `BOT_DOC_COLLECTION` | 文档知识库 collection 名（默认 `documents`） |
| `BOT_DOC_MINERU_ENDPOINT` | MinerU v4 精准解析 API 基地址（HTTP URL 直连，替代 Python SDK）；仅配 API Key 时回落 `https://mineru.net`；未配置时 PDF 自动降级 LangChain/pypdf |
| `BOT_DOC_MINERU_API_KEY` | MinerU API Bearer Token（API 管理页面自定创建） |
| `BOT_DOC_MINERU_AGENT_ENABLED` | MinerU Agent 轻量解析开关（默认开；免 Token、IP 限频、≤10MB/≤20 页），作为 PDF 解析第二重降级 |
| `BOT_DOC_CHUNK_SIZE` / `BOT_DOC_CHUNK_OVERLAP` | 文档切块大小与重叠 |
| `BOT_AUTO_REPLY` | 群聊非@消息自动回复总开关（默认关，可经 `/auto_reply` 运行时改） |
| `BOT_AUTO_REPLY_RANDOM_RATE` | auto_reply 非@消息的随机回复概率，默认 `0.3` |
| `BOT_AUTO_REPLY_COOLDOWN` | 同一会话两次 auto_reply 的最小间隔秒数，默认 `30` |
| `BOT_MCP_ENABLED` / `BOT_MCP_SERVERS_FILE` | MCP 外部工具（可选；server 定义在 `config/mcp_servers.json`） |
| `BOT_COMMAND_ENABLED` / `BOT_COMMAND_PREFIX` / `BOT_ADMIN_IDS` | 图外斜杠命令、前缀与管理员 ID |
| `BOT_SKILLS_ENABLED` / `BOT_SKILLS_DIR` | Markdown 技能模块（扫描 `skills/<name>/SKILL.md`） |
| `BOT_SKILLS_ALLOWLIST` / `BOT_SKILLS_DENYLIST` | 技能选择列表；逗号分隔，allowlist 空 = 全部，denylist 最后排除 |
| `BOT_TOOLS_ALLOWLIST` / `BOT_TOOLS_DENYLIST` | LLM 工具选择列表；`load_skill`/`unload_skill` 也按普通工具选择，改配置后重启生效 |
| `BOT_BASH_ENABLED` / `BOT_BASH_SHELL` | 技能脚本执行工具与 shell 路径（Windows Git Bash / WSL/Linux bash，默认 `bash`） |

## 运行时数据

`db/` 目录（`BOT_DB_DIR` 可改）启动时自动创建，库文件删除后重启会惰性重建。注意 `checkpoint.sqlite` 和 `memory.sqlite` 是真实数据，删除会丢会话状态与用户记忆：

- `checkpoint.sqlite` — LangGraph 会话状态
- `memory.sqlite` — 用户持久记忆（langgraph `AsyncSqliteStore`）
- `milvus.db` — 群聊历史向量（dense+sparse 混合检索）
- `embed_cache.sqlite` — 嵌入向量磁盘缓存

## 文档知识库导入

当前支持通过脚本离线导入 `.docx` / `.pdf` / `.xlsx` / `.txt` / `.json` 到独立的 `documents` collection：

```bash
# 导入单个/多个文件
uv run python scripts/import_documents.py docs/a.pdf docs/b.docx

# 只验证文件能否被解析和切分，不写入 Milvus
uv run python scripts/import_documents.py --dry-run docs/*.pdf
```

说明：

- 由于 `milvus-lite` 存在文件锁，建议在 Bot 停止时运行导入脚本；Bot 重启后即可通过 `search_documents` 检索。
- PDF 解析 3 重降级：① MinerU 精准解析 API（v4，需 `BOT_DOC_MINERU_ENDPOINT`/`BOT_DOC_MINERU_API_KEY`）→ ② MinerU Agent 轻量解析 API（v1，免 Token，`BOT_DOC_MINERU_AGENT_ENABLED`）→ ③ 本地 LangChain / pypdf；
- 其他格式优先使用 LangChain 生态的轻量 loader，未安装 `langchain-community` 时使用内置 fallback；
- 文档按内容哈希去重，重复导入自动跳过；
- 文档写入独立的 `documents` collection，不参与聊天记录按线程淘汰；
- Bot 启动后 LLM 可通过 `search_documents` 工具检索文档知识。

## 日志

启动时会自动创建根目录下的 `log/` 文件夹，bot 日志分三路输出：

- **终端**：保留，只打印 `INFO / WARNING / ERROR`（过滤 DEBUG）。
- **按天文件** `log/YYYY-MM-DD.log`：人类可读的详细日志（INFO 起），包含对话上下文中的 Human/AI/Tool Message，以及命令、RAG、MCP、工具执行等运行信息，便于回溯。
- **Web 控制台**：bot 每次启动会重建 `log/web.jsonl`（只保留本次运行日志），控制台后端从文件开头 tail 并经 `WS /api/logs/ws` 推送给前端；页面在 `http://localhost:5173/logs` 打开时即可看到本次运行已产生的全部日志，之后继续实时追加。

注意 `log/` 已加入 `.gitignore`，不会被提交到版本库。

## 测试

```bash
uv run python -m pytest
```

## 架构

```text
Satori 事件 -> SatoriAdapter -> Ingress -> MessagePipeline/WorkerPool
  -> Router -> Dispatcher
    - COMMAND       -> 图外命令 handler，不进图
    - REPLY         -> ContextCompactor -> LangGraph -> 发送回复 -> IndexWorker
    - CONTEXT_ONLY  -> graph.aupdate_state -> IndexWorker
    - SYSTEM/MEDIA/IGNORE -> 结束
```

同 thread 消息由 per-thread lock 串行；`BOT_MESSAGE_BATCH_MAX` 开启时，worker 在进入图前机会式合并连续突发消息，整批一次图调用、一条回复。批内命令仍按原位置单独执行，配置变更可作用于批内后续消息，RAG 索引按每条消息入队。

主要模块（统一在 `src/bot/package/` 下）：

- `core/` — `app.py`（运行时容器）与 `boot.py`（装配入口）
- `pipeline/` — 协议无关事件流水线（router/dispatcher/worker/pipeline）
- `platform/satori/` — Satori 协议模型、content 解析、WS/HTTP 客户端与事件归一化
- `utils/` — 纯技术横切设施：token 估算、日志/队列/重试与进程内领域事件总线
- `config/` — `BotConfig` 配置类
- `tools/` — 内部工具纯函数、`ToolSelection` 与 `build_tools` 装配
- `mcp/` — MCP server 配置加载与工具加载
- `commands/` — 图外斜杠命令上下文
- `conversation/` — 纯会话领域：`Conversation` 聚合根、领域事件与回复策略（`MessageRecord` / `IncomingMessage` / `ReplyPolicy` / `RouteDecision`）
- `domain/` — 共享领域对象、领域事件总线端口与仓库接口（events/ports/repositories/tasks/media）
- `knowledge/` — 群聊历史 hybrid search 与后台索引（RAG）；`DocumentStore` 实现 `DocumentRepository`，`TurnIndexProjection` 订阅会话领域事件
- `memory/` — 用户长期记忆上下文；`MemoryStore` 实现 `MemoryRepository`
- `orchestration/` — 会话编排：`BotState` 状态投影、`LangGraphConversationRepository` 适配器 + LangGraph 工作流组装与图节点
- `skill/` — 技能管理上下文与 `SkillSelection`
- `vision/` — 图片理解上下文

旧顶层路径、`src/bot/core/` 目录与 `src/bot/handler.py` 已删除，
所有导入统一使用 `src/bot/package/` 路径。更完整的架构约定、数据流与
开发细节见 `AGENTS.md`。
