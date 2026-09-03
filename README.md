# qq-bot

基于 **Satori 协议 + LLOneBot** 的 QQ 机器人，使用 **LangGraph** 编排多轮对话与工具调用。

机器人支持群聊/私聊的智能回复、长会话上下文管理（自动摘要压缩）、
群聊历史与文档的 **RAG 检索**、用户长期记忆、按需加载的技能（Skill）、
**MCP 外部工具**、图片视觉理解、文件发送与受控的 shell 执行，
并附带一个 **FastAPI 控制台后端 + Vue 3 Web 前端** 用于在线查看设置和实时日志。

- 语言 / 运行时：Python >= 3.12
- 包管理：`uv`（PyPI 镜像默认阿里云）
- 框架：LangChain / LangGraph / MCP / pydantic-settings / FastAPI / Vue 3

---

## 功能特性

- **消息流水线**：WebSocket 事件 → 队列（多 worker、按会话串行）→ 突发消息合并 → 路由 → 分发；
  自带 `event_id` 幂等去重，避免重放导致重复回复与重复索引。
- **多轮对话编排**：LangGraph 图（`describe_image → call_llm ⇄ tools → skill_manager → call_llm`），
  支持工具调用回环与上下文压缩。
- **上下文管理**：达到阈值自动摘要压缩，保留人设；`/compact` 可手动提前压缩，`/context` 可查看占用。
- **RAG（群聊历史检索）**：dense + sparse 混合检索（RRF 融合），LLM 主动触发检索，历史按会话保留并可裁剪。
- **文档知识库**：独立 `documents` 集合，支持导入 `.txt/.json/.docx/.xlsx/.pdf`，
  PDF 走 MinerU 精准解析 → MinerU Agent 轻量解析 → 本地 pypdf 三重降级。
- **用户长期记忆**：`remember / recall_user_memory` 工具，SQLite 持久化。
- **技能（Skill）**：按需加载 `skills/<name>/SKILL.md` 提示词包，支持白/黑名单选择。
- **MCP 外部工具**：经 `config/mcp_servers.json` 声明，密钥用 `${ENV_VAR}` 占位插值。
- **视觉理解**：OpenAI 兼容视觉 API 描述图片；主 LLM 多模态时可直读图片。
- **斜杠指令**：图外执行的命令模块（权限分级、不进对话图、不产生 RAG 索引）。
- **发送能力**：图片走标准 Satori，普通文件经 OneBot11 HTTP 兜底上传。
- **受控 Bash 工具**：供技能脚本执行，含危险命令拦截、路径白名单、超时与输出截断三道护栏。
- **控制台**：FastAPI 后端（设置读写、实时日志 WebSocket）+ Vue 3 Web 前端。

---

## 目录结构

```
.
├── main.py                    # Bot 薄入口：create_app() -> BotApplication.run()
├── console_api.py             # 控制台后端入口（FastAPI / uvicorn）
├── start_bot.sh               # 启动/停止/查看 Bot（后台或前台）
├── start_console.sh           # 启动/停止控制台后端 + Web 前端
├── start_all.sh               # 一键启动 Bot + 控制台 + Web
├── _common.sh                 # 服务启动/停止/状态管理共享库
├── pyproject.toml             # 项目配置与依赖
├── .env-template              # 环境变量文档化模板（复制为 .env）
├── config/
│   └── mcp_servers.json       # MCP server 定义（密钥用 ${ENV_VAR} 占位）
├── src/bot/package/           # 应用包主体
│   ├── core/                  #   BotApplication、create_app 装配、数据库、LLM 工厂
│   ├── pipeline/              #   协议无关事件流水线（队列/worker/路由/分发）
│   ├── platform/satori/       #   Satori 协议适配层（WS/HTTP/消息解析）
│   ├── config/                #   BotConfig（pydantic-settings）
│   ├── api/                   #   控制台 FastAPI（设置/日志/健康检查）
│   ├── commands/              #   图外斜杠指令模块（parser/registry/builtin/services）
│   ├── conversation/          #   纯会话领域（策略/聚合根/事件，不依赖 LangGraph）
│   ├── domain/                #   共享领域对象与端口
│   ├── knowledge/             #   RAG / 嵌入 / Milvus / 文档导入 / 索引 worker
│   ├── memory/                #   用户长期记忆
│   ├── orchestration/         #   LangGraph 图、节点、状态投影、上下文压缩
│   ├── skill/                 #   技能注册与加载
│   ├── tools/                 #   工具装配与内置工具（bash/检索/记忆/发文件）
│   ├── mcp/                   #   MCP 配置与工具加载
│   ├── vision/                #   视觉理解服务
│   └── utils/                 #   日志/队列/重试/事件总线等横切设施
├── skills/                    # 技能包（每个子目录一个 SKILL.md）
├── scripts/                   # 工具脚本（文档导入、依赖检查等）
├── web/                       # Vue 3 + Vite 控制台前端
├── tests/                     # pytest 测试
├── docs/                      # 文档（含 Satori API 说明）
└── db/                        # 运行时数据库（checkpoint / memory / milvus / embed_cache）
```

---

## 快速开始

### 1. 安装依赖

```bash
uv sync            # 安装全部依赖（含 dev 依赖）
```

### 2. 准备环境变量

```bash
cp .env-template .env
# 编辑 .env，至少配置：
#   BASE_URL / API_KEY / BOT_LLM_MODEL      — LLM（OpenAI 兼容接口）
#   BOT_WS_URL / BOT_API_BASE_URL           — Satori / LLOneBot 地址
#   BOT_ADMIN_IDS                           — 管理员 QQ ID（逗号分隔）
```

`.env-template` 是完整的配置文档，所有可用变量及其含义都注释在其中。
布尔值接受 `1/0/true/false/yes/no/on/off/空`；非法值会启动失败（有意 fail-fast）。

### 3. 启动

```bash
./start_bot.sh                # 后台启动 Bot（日志 log/bot.log）
./start_bot.sh --foreground   # 前台启动，Ctrl+C 停止
./start_bot.sh --stop         # 停止
./start_bot.sh --status       # 查看状态
```

同时启动控制台与 Web 前端：

```bash
./start_console.sh            # FastAPI 后端 + Vite 前端
./start_all.sh                # 一键启动 Bot + 控制台 + Web
./start_all.sh --stop         # 停止全部
```

也可以手动前台运行：

```bash
uv run python main.py                                   # Bot
uv run python console_api.py --port 8000                # 控制台后端
cd web && npm install && npm run dev                    # Web 前端
```

### 4. 访问控制台

| 服务 | 地址 |
|------|------|
| 控制台后端健康检查 | http://127.0.0.1:8000/api/health |
| Web 设置中心 | http://localhost:5173/settings |
| Web 实时日志 | http://localhost:5173/logs |

---

## 配置说明

所有配置通过环境变量（`.env`）注入，由 `BotConfig`（pydantic-settings）统一校验。
完整字段见 `.env-template`。主要分组：

| 分组 | 关键变量 | 说明 |
|------|----------|------|
| LLM | `BASE_URL` `API_KEY` `BOT_LLM_MODEL` `BOT_LLM_TEMPERATURE` | 主对话模型（OpenAI 兼容） |
| 传输 | `BOT_WS_URL` `BOT_TOKEN` `BOT_API_BASE_URL` `BOT_API_PLATFORM` | Satori / LLOneBot 连接 |
| 文件发送 | `BOT_ONEBOT11_API_BASE_URL` | send_file 的普通文件走 OneBot11 HTTP |
| 消息并发 | `BOT_MESSAGE_WORKER_COUNT` `BOT_MESSAGE_BATCH_MAX` `BOT_MESSAGE_DEDUP_SIZE` | 队列/合并/去重 |
| RAG / 嵌入 | `BOT_RAG_ENABLED` `BOT_EMBED_MODEL` `BOT_RAG_TOP_K` 等 | 群聊历史向量检索 |
| 文档导入 | `BOT_DOC_COLLECTION` `BOT_DOC_MINERU_*` `BOT_DOC_CHUNK_*` | 文档知识库 |
| 视觉 | `BOT_VISION_ENABLED` `BOT_VISION_MODEL` `BOT_VISION_MAX_IMAGES` | 图片描述 |
| MCP | `BOT_MCP_ENABLED` `BOT_MCP_SERVERS_FILE` | 外部工具 |
| 技能 | `BOT_SKILLS_ENABLED` `BOT_SKILLS_ALLOWLIST` `BOT_SKILLS_DENYLIST` | 提示词包技能 |
| 指令 | `BOT_COMMAND_ENABLED` `BOT_COMMAND_PREFIX` `BOT_ADMIN_IDS` | 斜杠指令 |
| 回复行为 | `BOT_AUTO_REPLY` `BOT_AUTO_REPLY_RANDOM_RATE` `BOT_AUTO_REPLY_COOLDOWN` | 群聊非 @ 自动回复 |
| Bash | `BOT_BASH_ENABLED` `BOT_BASH_SHELL` `BOT_BASH_ALLOWED_ROOTS` | 技能脚本执行护栏 |

嵌入/视觉的 `BASE_URL` / `API_KEY` 未单独配置时会自动回落主 LLM 的配置。

---

## 内置斜杠指令

| 指令 | 权限 | 说明 |
|------|------|------|
| `/help [command]` | 所有人 | 查看指令帮助 |
| `/ping` | 所有人 | 检查 bot 是否在线 |
| `/version` | 所有人 | 显示项目版本 |
| `/skills` | 所有人 | 列出已加载技能 |
| `/skill <name>` | 所有人 | 查看指定技能 |
| `/status` | 管理员 | 显示运行状态（模型/RAG/工具/队列等） |
| `/auto_reply [on\|off]` | 管理员 | 查看/设置全局自动回复开关 |
| `/clear` | 管理员 | 清空当前会话上下文（保留人设） |
| `/compact` | 管理员 | 提前总结并压缩当前会话上下文 |
| `/mcp` | 管理员 | 查看已加载的 MCP 工具 |
| `/tools` | 管理员 | 查看当前启用的 LLM 工具 |
| `/context` | 管理员 | 查看当前上下文占用情况 |

指令由路由层在文本进入对话图之前解析并执行：不经过 LLM 图、不产生 RAG 索引。

---

## 内置工具（LLM 可用）

工具由 `build_tools` 装配，可用 `BOT_TOOLS_ALLOWLIST` / `BOT_TOOLS_DENYLIST` 选择：

- `search_chat_history` — 群聊历史 RAG 检索（dense + sparse 混合）
- `search_documents` — 文档知识库检索
- `remember_user_memory` / `recall_user_memory` — 用户长期记忆读写
- `load_skill` / `unload_skill` — 技能按需加载/卸载
- `run_bash` — 在宿主执行 bash（受护栏约束，主要跑技能脚本）
- `send_file` — 发送文件（OneBot11 兜底）
- 以及 MCP 配置的外部工具

---

## 文档知识库导入

```bash
# 导入单个文件 / 目录 / 通配符
uv run python scripts/import_documents.py path/to/a.pdf path/to/b.docx
uv run python scripts/import_documents.py docs/*.pdf

# 只验证解析+切分，不写入 Milvus
uv run python scripts/import_documents.py --dry-run docs/*.pdf

# 指定集合名
uv run python scripts/import_documents.py --collection documents ./docs
```

支持 `.docx / .pdf / .xlsx / .txt / .json`，按内容哈希去重，已导入文件自动跳过。
> 注意：`milvus-lite` 有文件锁，建议在 Bot 停止时运行导入脚本。

---

## 开发

### 常用命令

```bash
uv run python -m pytest          # 运行测试
uv run ruff check                # 代码检查
uv run python -m ruff format .   # 代码格式化（如需要）
uv run python scripts/check_package_dependencies.py   # 包依赖方向检查
```

### 测试

测试位于 `tests/`，覆盖配置、流水线、路由、指令、对话领域、图节点、RAG、知识库、
记忆、技能、MCP、视觉、控制台 API 等模块。

### Web 前端（独立 README）

前端技术栈与开发联调说明见 [`web/README.md`](web/README.md)。

---

## 架构概览

```
WS 事件 → SatoriAdapter → IncomingMessage
  → MessageWorkerPool（队列/按会话串行/突发合并/去重）
  → Router.route_incoming() → RouteDecision
  → MessageDispatcher
       ├─ command   → 权限检查 → 命令 handler → 回复（不进图、不索引）
       ├─ system/media/ignore → 结束
       ├─ reply     → ContextCompactor.compact_if_needed() → graph.ainvoke()
       │               （describe_image → call_llm ⇄ tools → skill_manager → call_llm → END）
       │             → 立即发送回复 → 入队 IndexTurnTask → IndexWorker（RAG 索引）
       └─ context_only → ConversationRepository.append_record()（仅入上下文，不回复）
```

关键设计：

- **框架隔离**：`conversation/` 领域层不依赖 LangChain/LangGraph，`BotState` 是编排层状态投影。
- **上下文压缩**：达到 `BOT_SUMMARY_TRIGGER_RATIO` 阈值自动摘要，`RemoveMessage` 淘汰旧消息。
- **领域事件解耦 RAG 索引**：`ConversationTurnCompleted` 事件经事件总线驱动 `IndexWorker`，失败只降级不阻塞。
- **可选组件降级**：RAG / DocumentStore / Vision / MCP / Skill 初始化失败只降级，不阻断 Bot 启动。

数据库：

| 库 | 用途 |
|----|------|
| `db/checkpoint.sqlite` | 会话 checkpoint（LangGraph AsyncSqliteSaver） |
| `db/memory.sqlite` | 用户长期记忆 |
| `db/milvus.db` | 群聊历史 / 文档向量（milvus-lite） |
| `db/embed_cache.sqlite` | 嵌入磁盘缓存 |

---

## 相关文档

- [`docs/api/satori_api_docs.md`](docs/api/satori_api_docs.md) — Satori 协议 API 说明与本项目发送能力细节
- [`web/README.md`](web/README.md) — 控制台前端文档
- [`AGENTS.md`](AGENTS.md) — 面向 AI 编码助手的架构、数据流与开发约定
