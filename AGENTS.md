# AGENTS.md

This file provides guidance to AGENT when working with code in this repository.

## Commands

```
uv sync                  # install dependencies
uv run python main.py    # run the bot
uv run python -c "..."   # quick import / logic check
uv run ruff check        # lint（[tool.ruff] 见 pyproject.toml；BLE001/DTZ 忽略项是刻意设计）
uv run python -m pytest  # run tests
```

## Architecture

```
main.py                 # entrypoint — 装配 BotConfig / LLM / Graph / Handler / ContextCompactor / IndexWorker / RagService / MemoryStore
src/common/             # 共享配置 + 提示词（单一事实来源）
  config.py             #   BotConfig pydantic-settings（env 校验、严格布尔 Flag）
  mcp.py                #   load_mcp_servers_from_file — config/mcp_servers.json 加载 + ${VAR} 插值
  prompts.py            #   各提示词常量（persona / summary / *_TOOL_HINT / CURRENT_TIME_HINT / VISION / RETRIEVAL_TASK）
src/bot/
  transport/            # websocket（Satori WS 事件）+ http（send_message / call_api；send_file 普通文件走 OneBot11 HTTP）
  core/
    graph.py            # LangGraph 组装 → (graph, checkpointer)；仅保留 reply 流水线
    ingress.py          # SatoriMessageIngress — EventBody 校验 → IncomingMessage（生成 event_id/trace_id）
    router.py           # route_incoming — 协议无关路由（RouteDecision 数据对象在 src/domain/bot/router.py）
    dispatcher.py       # MessageDispatcher — RouteDecision → 命令/graph/context/system/media 流水线
    worker.py           # MessageWorkerPool — 消息队列 + thread lock + Router + Dispatcher
    compaction.py       # ContextCompactor — 图外上下文压缩（自动 compact_if_needed / 命令 force_compact）
    llm.py              # ChatOpenAI 工厂（读 BASE_URL / API_KEY）
    memory.py           # MemoryStore — AsyncSqliteStore 按用户 kv 记忆
    rag/                # 群聊历史向量检索：embedder(Ollama) / cache / service / milvus / index_worker
    vision/             # VisionService — Ollama 视觉描述 + 多模态 data-url 下载
    utils/              # 纯函数：context(token 估算) / content_parser / routing(回复判定)
    mcp/                # client.load_mcp_tools（逐 server 降级）
    skills/             # loader(SkillRegistry 扫描) + tools(load/unload 纯函数；Skill 数据对象在 src/domain/bot/skill.py)
    commands/           # 图外斜杠指令：parser / registry / builtin（数据模型在 src/domain/bot/command.py）
    nodes/              # 图节点：llm_node(call_llm) / action_node(describe_image, skill_manager)；detect_intent/summarize/index_turn 保留 helper 不挂图
    tools/              # factory.build_tools + search_chat_history / user_memory / send_file 纯函数
  handler.py            # MessageHandler — 协议适配门面：EventBody → Ingress → WorkerPool
src/domain/             # 领域/协议数据对象（懒加载）：bot/（state、message、identity、index_task、content、skill、command、router、bash）、satori/
db/                     # checkpoint.sqlite / memory.sqlite / embed_cache.sqlite / milvus.db
```

## Data flow

```
WS 事件 → EventBody → MessageHandler.handle() → SatoriMessageIngress → IncomingMessage（event_id/trace_id）
  → MessageWorkerPool 消息队列（N 个 asyncio worker，按 thread_id 锁串行；锁内机会式合并同会话连续消息，
     上限 BOT_MESSAGE_BATCH_MAX，整批一次路由/一次图调用/一条回复；命令消息在批内原位单独执行）
  → Router.route_incoming() → RouteDecision
  → MessageDispatcher
      command      → 权限 → 命令 handler → 回复；不进图、不自动压缩、不产生 RAG 索引
      system/media/ignore → 结束
      reply / context_only → ContextCompactor.compact_if_needed()（同一 thread lock 内同步；整批只查一次）
        reply        → graph.ainvoke（突发合并轮：messages 注入整批 HumanMessage，一次回复覆盖全批）
                       → describe_image → call_llm → tools → skill_manager → call_llm（按需回环）→ END
                    → 立即 send_message（Dispatcher 消费 result["reply_text"]，无 send_reply 节点）
                    → 逐条 enqueue IndexTurnTask（bot 回复挂在每条消息上）→ IndexWorker
        context_only → graph.aupdate_state({"messages": [HumanMessage...]}) → 逐条 enqueue IndexTurnTask(bot_reply="")
```

## Databases

| 库 | 管理方 | 用途 |
|---|---|---|
| db/checkpoint.sqlite | AsyncSqliteSaver | 会话 checkpoint（checkpoint/writes/user_memory 表）|
| db/memory.sqlite | MemoryStore | LLM 经 remember/recall 工具写的长期用户事实（store 表，namespace `("user", uid)`）|
| db/milvus.db | MilvusStore | 群聊历史 dense+sparse 向量（milvus-lite 单文件）|
| db/embed_cache.sqlite | EmbeddingCache | 嵌入磁盘缓存（key=sha256(model+任务前缀+角色+原文)）|

## Session vs Thread

thread_id = `platform:guild:channel`，每频道隔离会话历史（session_id 已移除，日志打 thread_id）。

## Key patterns

**Lazy-loading `domain/`**：`src/domain/__init__.py` 用 `__getattr__` + `_module_map` 按名懒加载子模块。新增领域数据对象或 Satori 模型/参数时同步 `__all__` 与 `_module_map`。

**Node DI**：`graph.py` 用 `functools.partial` 注入（非闭包）；节点文件均为独立 `async def(state, ...) -> dict`。

**create_graph 返回 `(graph, checkpointer)`**：checkpointer 生命周期归 main.py，不在 create_graph 内关闭。服务注入：`rag_service`/`memory_store`/`skill_registry`/`mcp_tools` 进 call_llm_node（工具绑定+技能注入层）与 tools（ToolNode 执行）；`vision_service` 进 describe_image；`file_sender` 进 tools；`skill_registry` 另由 ContextCompactor 的 summarize_node 与命令层复用（token 口径一致）。

**SystemMessage 动态注入**：`call_llm_node` 每次调用由 `build_system_messages` 现构多层 system 并前置，只把 AIMessage 落 state。层级：①当前时间提示（CURRENT_TIME_HINT，LLM 算相对时间/`hours` 的基准）→ ②conversation_summary → ③技能索引 → ④激活技能正文 → ⑤记忆工具提示 → ⑥MCP 工具提示。不变量：
- system 为局部变量，**绝不持久化**；persona 恒在 messages[0]（`.format(bot_name=...)`），改动即时生效
- checkpoint 只存 Human/AI/ToolMessage
- `estimate_context_tokens` 复用同一函数，token 估算与实际注入不偏离（`now` 仅供测试）

**RAG（群聊历史检索）**：
- LLM 主动触发：`search_chat_history` 绑定为工具，返回 tool_calls 时经 ToolNode 执行并回环；`tool_rounds` 达 `rag_max_agent_rounds` 强制收尾
- 检索双模式：`hybrid_search`（dense ANN+score 阈值 + sparse BM25/jieba，RRF k=60 融合，当前群优先、不足跨群补齐）；属性检索（`search_by_user`，milvus expr 过滤：`person`/`content_keyword`/ISO 时间窗，thread_id=None 跨全部群）。`hours`/`start_time`/`end_time` 入口 `normalize_time` 规范化
- 索引任务：graph 返回后 handler 构造 `IndexTurnTask` 入队 `IndexWorker`（独立 asyncio.Queue + 单 FIFO consumer）；reply 轮 2 条（用户+Bot）、context_only 1 条（仅用户）、图片 user_message 追加 `[图片]` 占位符；纯媒体但有 `reply_text` 时仍存 bot 回复，两者皆空才跳过。timestamp 为 ISO `YYYY-MM-DD HH:MM:SS`（字典序==时间序）；记录显式 sender/receiver（`sender_id/name`、`receiver_id/name`）。队列满/失败只降级丢任务，不阻塞消息 worker。
- 嵌入：Ollama `qwen3-embedding`，Query/Document 共用 Instruct 前缀，按 `(model, 任务前缀, 角色, 原文)` 哈希落盘缓存（换模型/改 RETRIEVAL_TASK 自动失效）；嵌入/视觉 base URL 独立配置，`OLLAMA_BASE_URL` 仅作旧共用地址兼容回落
- env：`BOT_RAG_ENABLED`/`BOT_EMBED_MODEL`/`BOT_EMBED_BASE_URL`/`BOT_EMBED_DIMENSIONS`/`BOT_EMBED_CACHE_ENABLED`/`BOT_EMBED_CACHE_MAX_ENTRIES`/`BOT_RAG_TOP_K`/`BOT_RAG_SCORE_THRESHOLD`/`BOT_RAG_RETENTION_PER_THREAD`/`BOT_RAG_MAX_AGENT_ROUNDS`；视觉 `BOT_VISION_ENABLED`/`BOT_VISION_MODEL`/`BOT_VISION_BASE_URL`/`BOT_VISION_MAX_IMAGES`/`BOT_VISION_TIMEOUT`；多模态 `BOT_LLM_MULTIMODAL`（0=本地视觉/1=主 LLM）；`OLLAMA_BASE_URL` 旧共用地址兼容回落
- MCP：`BOT_MCP_ENABLED`/`BOT_MCP_SERVERS_FILE`/`BOT_MCP_TOOL_NAME_PREFIX`；server 定义集中在可提交的 `config/mcp_servers.json`（`{"servers": {...}}`，密钥用 `${ENV_VAR}` 占位），加载 `src/common/mcp.py::load_mcp_servers_from_file`（相对路径按项目根解析、缺失/损坏降级空、插值缺变量→空串；env 必传、不读 os.environ）；main.py 用 `dotenv_values(find_dotenv())` 读 .env 内容做插值源，再 `client.py::load_mcp_tools` 加载；加载后注入 MCP_TOOL_HINT 引导

**记忆工具**：注入 MemoryStore 后 call_llm 绑定 `remember/recall_user_memory` 工具 + MEMORY_TOOL_HINT，LLM 自行决定读写；工具暴露 `user_id`/`user_name` 参数，批内可指定目标发言者，缺失时回退最近一条 HumanMessage 元数据，底层官方 AsyncSqliteStore 全 async。旧"图前全量注入 + 图外抽取"方案已移除。

**技能模块**：`Skill` 纯数据对象在 `src/domain/bot/skill.py`；`SkillRegistry.from_directory` 扫描 `skills/<name>/SKILL.md`（frontmatter name/description+正文；目录缺失→空注册表不崩）。build_tools 包装 `load_skill`/`unload_skill`（纯函数只返回正文/确认）；load 成功后 `skill_manager` 节点把 skill_name 追加进 `active_skills`（tools→skill_manager→call_llm **逐轮**回环接线，只增不改、不设 reducer）。注入层：技能索引 + 激活正文。**关键约束：handler 绝不注入 active_skills**（输入 state 覆盖 checkpoint 会清零持久化激活），节点一律 `state.get("active_skills", [])`。

**指令模块（图外斜杠指令）**：命令数据模型统一在 `src/domain/bot/command.py`（`Command`/`ParsedCommand`/`CommandActor`/`CommandContext`/`CommandResult`/`CommandServices`），`src/bot/core/commands` 只保留 parser/registry/builtin。env `BOT_COMMAND_ENABLED`(默认1) / `BOT_COMMAND_PREFIX`(默认`/`，min_length=1 空串 fail-fast) / `BOT_ADMIN_IDS`(逗号分隔)。Router 在文本进图前解析 `prefix+name+args`；命中注册命令→权限检查（admin 命令仅 admin actor，CLI actor 隐式 admin）→handler→回复，**不进图、不产生 RAG 索引**；未注册回落对话流。命令名须字母开头 `[a-z][a-z0-9_-]*`（`/123`、`/--` 回落）；参数 shlex **POSIX** 分词（`\` 转义，Windows 路径 `C:\tmp\x`→`C:tmpx` 会吞反斜杠，V1 无路径命令）。V1：`/help /ping /version /skills /skill /status /auto_reply /clear /compact /mcp /context`（status/auto_reply/clear/compact/mcp/context 为 admin，auto_reply 运行时改写 BOT_AUTO_REPLY）。`/skill` 正文 everyone 可见（截断 2000 字，视为非机密；含敏感内容需评估暴露面）。`/clear` 用 `graph.aupdate_state` 保留 persona，只清空 messages/conversation_summary/active_skills/tool_rounds，不删 RAG 历史与用户记忆；`/compact` 调 `ContextCompactor.force_compact`（读 checkpoint → `summarize_node(force=True)` → `aupdate_state` 写回）；`/mcp` 列出 main.py 启动时捕获的 `mcp_tool_names`；`/context` 用 `estimate_context_tokens` 报告占用/剩余/摘要/技能/自动压缩阈值。命令层与 Satori 解耦，CLI 可直接构造 admin actor 复用。

**Node 分类约定**：`llm_node/`（调 LLM）· `action_node/`（确定性无 LLM，含 describe_image/skill_manager；detect_intent/summarize/index_turn 保留 helper 不挂图）· `tools`（prebuilt ToolNode 统一执行全部工具）· `mcp/`（外部工具加载，单 server 失败降级）· `subgraph/`（嵌套子图）。

## Gotchas

- **`domain/` 包**：领域/协议数据对象统一在 `src/domain/`，包名避免与内置 `object` 混淆。始终 `from domain.*` 导入。
- **@提及**：Satori 用 `<at id name/>` 非 `@name`；回复判定基于 `parse_mentions` **顶层提及集合** `{id: 昵称}`（引用/转发不计），Router/decide_reply 以 bot_id 为主、bot_name 兜底。LLM 输入渲染 `@昵称(id)`（all→所有成员、here→在线成员）；`llm_text` 每轮必注入，Router/handler 直接消费。
- **content_parser**：`to_llm_text` 媒体→占位符、@→@昵称(id)、链接→`标题 (url)`、其余标签全剥留文本；`clean_text` 剥全部标签含闭合与注释。剥离单一来源 `_TAG_RE`，`_AT_TAG_RE` 仅 at 提取/渲染。
- **回复判定树（纯确定性，无 LLM router）**：Router/decide_reply 判定：私聊/顶层@为显式请求，始终回复并绕过 auto_reply random/cooldown；file/audio/video 永不回复；群聊非@文本和图文混合在 auto_reply=false 时入上下文+索引但不回复，纯图片无文本走 MEDIA 流水线（不上下文、不回复、不索引）；auto_reply=true 时由 `BOT_AUTO_REPLY_RANDOM_RATE` + `BOT_AUTO_REPLY_COOLDOWN` 决定是否回复，未命中仍保留上下文/RAG。图片 RAG 统一使用 `[图片]` 占位符，不存 URL/base64/视觉描述。
- **视觉节点双模式**：HumanMessage 携带各自的 `image_srcs`/`auto_reply`；dispatcher 用 `vision_target_count` 限定本轮图输入，describe_image 只处理本轮追加的 HumanMessage 并逐条处理批内全部图片，不扫描历史 checkpoint。`vision_desc` 为 `list[ImageDescription]`（`image_src` + `description`），每张图有明确对应。`llm_multimodal=0`（默认）把 `[图片]` 原位替换为 `[图片：描述]`；`=1` 图片转 data URL 进主 LLM（本地视觉仅产 vision_desc）。`auto_reply=True` 图片轮跳过本地视觉：多模态主 LLM 直接看图，非多模态保留 `[图片]` 占位符且不产 vision_desc。多模态 content 块列表**一律经 `content_to_text` 归一化为字符串**（透传列表会在摘要/后台索引 `.strip()` 崩溃）；摘要格式化只取 text 块，绝不带 base64。VisionService 单张失败返回 `""` 不抛。`[图片：{desc}]` 由 describe_image 拼装供 LLM；后台索引 user_message 统一保留 `[图片]` 占位符。
- **uv**：PyPI mirror = mirrors.aliyun.com（pyproject `[[tool.uv.index]]`）；Python >=3.12。
- **`.env`**：`BASE_URL` + `API_KEY`（非 GO_*），`.env-template` 是文档化 schema。
- **严格布尔解析（Flag）**：布尔 env 只接受 `1/0/true/false/yes/no/on/off/空`，非法值抛 ValidationError 启动崩——有意 fail-fast。
- **db/**：启动自动建目录；库文件全部惰性重建（删除→重启重建）。checkpoint.sqlite 含会话状态、memory.sqlite 含用户记忆，**是真数据**。`BOT_DB_DIR`(默认 `db`)。
- **milvus-lite**：集合 `chat` 双向量（`vector` HNSW/COSINE + `sparse` BM25，text jieba analyzer），timestamp TEXT ISO，`_prune_thread` 超 `rag_retention_per_thread` Python 侧排序删最旧（query 不支持 order_by，动态字段须先 `list()` 物化）。`_ensure_collection` 校验 vector dim，维度漂移 DROP 重建；末尾统一 `load_collection`（新进程集合默认 released，查询前必须 load，跨进程脚本同）。新进程 hit 动态字段在 `entity` 子字典，`_dense_hit`/`_sparse_hit` 统一展平。**pymilvus 直连须覆盖 `grpc_options` 防 `too_many_pings` GOAWAY**（官方激进 keepalive 与 milvus-lite 默认 ping 策略冲突；MilvusStore 已处理，新增客户端须同样覆盖）。
- **工具定位**：纯函数在 `tools/search_chat_history.py`、`tools/user_memory.py`、`skills/tools.py`、`tools/run_bash.py`、`tools/send_file.py`；`build_tools` 包装为 BaseTool（闭包绑服务 + thread_id/channel_id 经 InjectedState 注入，记忆工具的 user_id/user_name 由 LLM 传参、缺失回退 HumanMessage 元数据 + 异常降级）。ToolNode `handle_tool_errors`：除 `ToolInvocationError`（原样返回逐字段校验信息供 LLM 纠正）外统一降级、按类名记日志（防 Tavily URL 泄漏）。MemoryStore 首次启动自动迁移旧 `user_memories` 表进 `store` 后 DROP。
- **run_bash（bash 工具）**：LLM 在 bot 宿主执行 bash 命令，主要跑 skill 脚本与 skill 内环境配置。本机 `.env` 显式指向 `E:/Git/bin/bash.exe`（Git Bash）；未配置时 `bash` 可能解析为 WSL，技能调用 Windows Python 时须用 `WSLENV` 共享 `.env` 变量（见 jmcomic SKILL.md）。三道护栏按序：① `DANGEROUS_PATTERNS` 正则拦截危险命令（返回具体文案）② cwd `resolve()` 白名单（project_root 恒允许，`BOT_BASH_ALLOWED_ROOTS` 扩展，越界返回提示）③ `asyncio.wait_for` 超时杀进程 + 输出截断到 `bash_max_output`。cwd 走 subprocess 参数、不拼命令串；每次调用独立新 shell，`cd`/`export` 不跨调用持久（环境配置靠文件系统）。编码 UTF-8 回落 GBK。config：`BOT_BASH_ENABLED`(默认1) / `BOT_BASH_SHELL` / `BOT_BASH_TIMEOUT` / `BOT_BASH_MAX_OUTPUT` / `BOT_BASH_ALLOWED_ROOTS`。护栏拦截/超时/越界是正常返回，真异常（shell 不存在）由 factory 降级「工具执行失败。」。
- **Persona fallback**：`config.persona_prompt.strip() or DEFAULT_PERSONA_PROMPT`（默认是真实提示词非空串），`BOT_PERSONA_PROMPT=""` 强制回落；均用 `{bot_name}` 占位符。
