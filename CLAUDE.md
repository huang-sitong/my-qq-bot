# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```
uv sync                  # install dependencies
uv run python main.py    # run the bot
uv run python -c "..."   # quick import / logic check
uv run ruff check        # lint（[tool.ruff] 见 pyproject.toml；BLE001/DTZ 忽略项是刻意设计）
```

## Architecture

```
main.py                      # entrypoint — wires BotConfig, LLM, Graph, Handler, RagService, MemoryStore
common/                      # shared config + prompts (single source of truth)
  config.py                  #   BotConfig pydantic-settings (env-var schema + validation)
  mcp.py                     #   parse_mcp_servers — BOT_MCP_SERVERS JSON 解析（BotConfig validator）
  prompts.py                 #   DEFAULT_PERSONA_PROMPT, ROUTER_PROMPT, SUMMARY_PROMPT, MEMORY_TOOL_HINT, MCP_TOOL_HINT, CURRENT_TIME_HINT, VISION_PROMPT, RETRIEVAL_TASK
bot/
  transport/websocket/       # Satori WS events: connect, identify, reconnect
  transport/http/            # Satori HTTP API: send_message, generic call_api
  core/
    graph.py                 # LangGraph assembly: creates (graph, checkpointer)
    llm.py                   # ChatOpenAI factory (reads BASE_URL / API_KEY from BotConfig)
    memory.py                # MemoryStore — langgraph AsyncSqliteStore 封装的按用户 kv 记忆 (memory.sqlite)
    rag/                     # 群聊历史 RAG（向量检索）
      embedder.py            #   EmbeddingService — Ollama qwen3-embedding，Instruct 前缀
      cache.py               #   EmbeddingCache — content 哈希 → 向量磁盘缓存 (embed_cache.sqlite)
      service.py             #   RagService — index_turn / search / hybrid_search 组合接口
      milvus.py              #   MilvusStore — milvus-lite dense+sparse 混合检索 (db/milvus.db)
    vision/                  # Ollama 视觉模型（图片描述）
      service.py             #   VisionService — 下载→base64→Ollama /api/generate；模块级 download_images_as_data_urls（多模态主 LLM 用）
    utils/                   # Pure utility functions (no state)
      context.py             #   token estimation + message formatting for summarization
      content_parser.py      #   Satori content 解析逻辑（媒体→占位符、@→@昵称(id)、链接→标题 (url)、其余全剥；类型见 object/bot/content.py）
      routing.py             #   确定性回复判定（decide_reply 按顶层提及集合 id+昵称混合 / keep_in_context / route_after_detect）
    mcp/                     #  MCP 外部工具加载（langchain-mcp-adapters，远程/stdio 多 server）
      client.py              #   load_mcp_tools — 逐 server 降级加载，返回 BaseTool 列表
      config.py              #   build_mcp_connections — 额外 server + Tavily 远程端点合并（纯函数）
    skills/                  #  技能模块（提示词包）：SkillRegistry 扫描加载 + load/unload 工具
      loader.py              #   Skill/SkillRegistry — 解析 skills/<name>/SKILL.md 的 frontmatter + 正文
      tools.py               #   load_skill / unload_skill 纯函数（只返回正文/确认，状态由 skill_manager 写回）
    nodes/                   # Graph nodes classified by execution mechanism:
      llm_node/              #   call_llm — invoke LLM（router 保留但未接线）
      action_node/           #   detect_intent (routing), summarize (context window management), index_turn (RAG 入库), skill_manager (技能激活写回)
      subgraph/              #   nested subgraphs (future)
    tools/                   # Tool definitions imported by graph / tools node
      factory.py             #   build_tools — 内部纯函数包装为 BaseTool（InjectedState 注入 + 异常降级）
      search_chat_history.py #   search_chat_history 纯函数（无 TOOL_SCHEMA，schema 由签名推断）
      user_memory.py         #   remember/recall_user_memory 纯函数（无 TOOL_SCHEMA）
  handler.py                 # MessageHandler — ingress: validation → queue → graph → reply（RAG 索引在图内 index_turn）
object/                      # protocol data-objects (lazy-load via __getattr__)
  bot/state.py               #   BotState TypedDict (graph state schema)
  bot/content.py             #   MessageKind/Attachment/ParsedContent（消息分类领域类型）
  satori/                    #   Satori protocol: enums, models, events, API endpoints
db/                          # runtime databases (checkpoint.sqlite, memory.sqlite, embed_cache.sqlite) + db/milvus.db
```

### Data flow

```
WebSocket event → SatoriClient → MessageHandler.handle()
  → validation + enqueue → worker dequeues（按 thread_id 加锁串行化）
  → graph.ainvoke(state, thread_id)
    → detect_intent (action_node)  ← 确定性三路（无 LLM router）：text/image 对 DIRECT/顶层@提及 回复；file/audio/video 永不回复；媒体非回复不入上下文
      → 条件边：should_respond → describe_image；非回复文本 → summarize；其余 → END
    → describe_image (action_node) ← 图片回复路径：下载→Ollama qwen3-vl 描述→[图片] 原位替换（vision_desc 供索引）；非图片/禁用 no-op
    → call_llm (llm_node)          ← dynamic SystemMessage injection
      tools (ToolNode) → skill_manager (action_node) ← call_llm 返回 tool_calls 时由 prebuilt ToolNode 统一执行（RAG/记忆/MCP/技能工具），skill_manager 把 load/unload 调用写回 active_skills，逐轮回环到 call_llm
    → summarize (action_node)      ← token threshold check → progressive summary
    → index_turn (action_node)     ← 回复轮索引 2 条（用户+Bot）；群聊非@文本索引 1 条（仅用户）；纯媒体不索引
  → send reply via SatoriApiClient
```

### Three-database design

| File | Managed by | Purpose |
|---|---|---|
| `db/checkpoint.sqlite` | LangGraph `AsyncSqliteSaver` | Conversation state checkpoints (tables: `checkpoint`, `writes`, `user_memory`) |
| `db/memory.sqlite` | `MemoryStore`（langgraph `AsyncSqliteStore`） | Long-term user facts written by LLM via remember/recall tools（表 `store`，namespace `("user", user_id)`，value 存 `{"value": str}`） |
| `db/milvus.db` | `MilvusStore` | 群聊历史向量（dense+sparse，milvus-lite 单文件） |
| `db/embed_cache.sqlite` | `EmbeddingCache` | 嵌入向量磁盘缓存（表 `embed_cache`，key=sha256(model+任务前缀+角色+原始内容)，text 列只存原始内容） |

### Session vs Thread

- **session_id**（已从 BotState 移除） = `platform:guild:channel:user` — 曾仅用于日志；现日志改打 thread_id，省 checkpoint 冗余
- **thread_id** (checkpoint isolation):
  - All chats → `platform:guild:channel` (per-channel conversation history, guild-reserved for multi-platform)

## Key patterns

### Lazy-loading `object/` package

`object/__init__.py` uses `__getattr__` + `_module_map` so that importing a single name (e.g. `from object.satori import EventBody`) doesn't load all sub-modules. When adding new Satori models or API params, update both the `__all__` list and `_module_map` in the corresponding `__init__.py`.

### Node dependency injection

Graph nodes in `bot/core/nodes/` use `functools.partial` for injection (not closures). In `graph.py`:
- `call_llm_node` bound with `partial(call_llm_node, llm=llm, tools=tools, use_memory=use_memory, use_mcp=use_mcp, bot_config=config, skill_registry=skill_registry)`
- `summarize_node` bound with `partial(summarize_node, llm=llm, bot_config=config, skill_registry=skill_registry)`
- `skill_manager_node` bound with `partial(skill_manager_node, skill_registry=skill_registry)`
- `tools` node = `ToolNode(build_tools(rag_service=rag_service, memory_store=memory_store, mcp_tools=mcp_tools, skill_registry=skill_registry))` — 内部工具闭包绑定服务 + `InjectedState` 注入 thread_id/user_id，MCP 工具直接并入

Each node file is a standalone `async def(state, ...) -> dict`.

### `create_graph()` returns a tuple

`create_graph(llm, config, db_dir="db", rag_service=None, memory_store=None, vision_service=None, mcp_tools=None, skill_registry=None)` returns `(graph: CompiledStateGraph, checkpointer: AsyncSqliteSaver)`. The `main.py` caller manages the checkpointer lifecycle — do not close it inside `create_graph`. `rag_service` / `memory_store` 与 `skill_registry` 传入 `call_llm_node`（工具绑定 + 技能注入层）与 `tools`（ToolNode，tool execution）参与图级工具回环；`skill_registry` 同时传给 `summarize_node`（token 估算一致）与 `skill_manager_node`（激活写回）。`mcp_tools` 为 `BaseTool` 列表，由 `bot/core/mcp/client.py::load_mcp_tools` 加载后传入 `call_llm`（绑定）与 `ToolNode`（执行）。

### SystemMessage injection

`call_llm_node` builds a **multi-layer** SystemMessage list **dynamically each invocation**, prepends it to `state["messages"]`, and returns only the `AIMessage` to state:

```python
# Persona is formatted with bot_name for self-awareness
persona = state["persona"].format(bot_name=state.get("bot_name", ""))
system_msgs = build_system_messages(
    persona, summary,
    skill_registry=skill_registry,
    active_skills=state.get("active_skills", []),
)
# Layer 1: current time hint（CURRENT_TIME_HINT，动态注入当前时间+星期，供 LLM 算
#          相对时间/hours/start_time/end_time 的基准；LLM 不知道墙钟时间）
# Layer 2: conversation_summary (from summarize_node)
# Layer 3: skill index（SKILL_INDEX_HINT + SkillRegistry.index_text，仅 skill_registry 非空时）
# Layer 4: active skills bodies（SKILL_ACTIVE_HINT + 各技能正文，仅 active_skills 非空时）
# Layer 5: memory tools usage hint (MEMORY_TOOL_HINT, 仅注入 memory_store 时)
# Layer 6: MCP external tools hint (MCP_TOOL_HINT, 仅注入 mcp_tools 非空时)
```

- SystemMessages are **local variables** — never persisted to checkpoint
- Persona is always at `messages[0]` regardless of conversation length — immune to context-window truncation
- Persona changes take effect immediately (no `has_persona` gate)
- Checkpoint stores conversation history (HumanMessage + AIMessage + ToolMessage)，not system instructions
- `DEFAULT_PERSONA_PROMPT` uses `{bot_name}` placeholder — formatted at invocation time（`ROUTER_PROMPT` 已随 router 摘除停用，仅保留文件）
- 层级统一由 `build_system_messages`（`bot/core/utils/context.py`）构造：`estimate_context_tokens` 复用同一函数，token 估算与实际注入永不偏离（`now` 参数仅供测试固定时刻）

### RAG（群聊历史检索）

- **触发**：`rag_enabled`（默认开启）。注入 `RagService` 后，`call_llm` 绑定 `search_chat_history` 工具（`memory_store` 注入时同时绑定 `remember_user_memory` / `recall_user_memory`），**LLM 自行决定何时检索**。若返回 `tool_calls`，条件边路由到 `tools（ToolNode）` 执行，回边到 `call_llm` 继续；`tool_rounds`（总工具轮次计数）达到 `rag_max_agent_rounds` 后走无工具路径强制收尾。内部工具经 `build_tools` 包装为 `BaseTool`、schema 由签名推断。
- **索引**：图内 `index_turn` 节点（action_node，位于 summarize 与 END 之间）对**回复轮**写入 2 条（用户消息 + Bot 回复）、对**群聊非@文本**只写入 1 条（仅用户消息，`bot_reply` 为空由 `RagService.index_turn` 配对过滤）；用户内容来自 handler 预计算的 `clean_text`（剥全部元素标签 + unescape，`parse_content` 产出），`index_turn` 直接消费、不再图内解析 raw_content。**图片回复轮**（`content_kind=="image"` 且有 `vision_desc`）将描述并入用户消息（` [图片：{desc}]`）再入库。**纯媒体（clean_text 为空）但有回复时**（多模态 vision 关闭的图片轮）仍把 `reply_text` 作为 assistant 记录入库——承载主 LLM 对图的理解，`RagService.index_turn` 过滤空对只留 bot 记录；仅当 clean_text 与 reply_text **都为空**才整轮跳过。索引失败仅降级（`RagService.index_turn` 内部吞异常）。记录显式建模发送者/接收者（`sender_id/name`、`receiver_id/name`，替代旧 `user_id/user_name/role`）：用户消息 sender=用户、receiver=bot（回复轮）或空（群广播）；bot 回复 sender=bot 名、receiver=用户。`bot_id/bot_name` 由 `index_turn` 从 state 注入，使"按 bot 名查 bot 发言"成为可能。`timestamp` 落库为 **ISO 字符串** `YYYY-MM-DD HH:MM:SS`（本地时区，定宽零填充 → 字典序==时间序，表达式直接 `>= / <=` 比较），替代 epoch 整数；展示层 `_format_time` 截到分钟。
- **嵌入**：Ollama `qwen3-embedding`（`embedder.py`）。Query 与 Document **共用** `Instruct: 检索群聊历史中与问题最相关的消息` 前缀以保持向量空间一致 —— qwen3 是对话模板模型，检索必须加 Instruct 前缀（当前无实机验证测试，`tests/test_embed_cache.py` 仅覆盖缓存侧——text 列不带前缀）。嵌入结果按 `(model, 任务前缀, 角色, 原始内容)` 哈希落盘缓存（`cache.py`，`db/embed_cache.sqlite`，key 覆盖 model/任务/角色 故换模型、改 RETRIEVAL_TASK 或 Query/Document 互换均自动失效；**text 列只存原始内容**，不带 Instruct 前缀），重复文本命中缓存不再调 Ollama。
- **检索策略**（`RagService.hybrid_search`）：dense 语义检索（query 嵌入 → `vector` 字段 ANN，候选按 `score ≥ rag_score_threshold` 过滤）+ sparse 词法检索（`content_keyword or query` 直接进 BM25 函数，jieba 分词，无阈值），二者经 **RRF** 融合（`rrf.py`，k=60），**当前群聊优先，本群命中不足时用跨群结果补齐**（`thread_id=None` 跨全部群，expr 过滤仍生效）。另有**属性检索**（`RagService.search_by_user`，milvus expr 过滤，`_build_expr` 组装 + `_esc` 转义）：**跨全部群检索**（`thread_id=None` 取消群聊限制；给群 id 则限定单群）、`person` 前缀匹配 sender_name 或 receiver_name（查"某人说过什么 / bot 回了谁"）、`content_keyword` 作 sparse 信号（查"谁说过 xx"）、ISO 时间窗口（`start_time`/`end_time`，表达式 `>= / <=` 比较，字典序）。**时间窗口在语义检索同样生效**（filter expr 直接带进 search）。
- **工具闭环**：`search_chat_history(query, rag_service, thread_id, user_name, hours, content_keyword, start_time, end_time)` 是纯函数，**双模式**——指定 `user_name`/`content_keyword` 走属性检索（milvus expr 过滤，**thread_id 置 None 跨全部群**），否则走 hybrid_search 语义检索（dense+sparse+RRF，当前群优先、不足跨群补齐）；`start_time`/`end_time` 为 ISO 时间窗口，**两种模式均生效**，入口经 `normalize_time` 规范化（`fromisoformat` 接受 `YYYY-MM-DD`/T 分隔，非法输入返回错误提示）；`hours` 相对窗口在 service 层换算为 ISO 起点。**LLM 计算相对时间/时间窗的基准来自 call_llm 注入的 `CURRENT_TIME_HINT` 当前时间提示**（LLM 不知道墙钟时间，没有该提示 `hours`/`start_time` 无从算起）。`tools（ToolNode）` 经 `InjectedState` 注入 `thread_id`，`rag_service` 由 `build_tools` 闭包绑定注入；工具调用消息（AIMessage + ToolMessage）持久化到 checkpoint。结果渲染 `[时间] 发送者 → 接收者: 内容`（receiver 空时只显示发送者；**跨群结果加 `[来源群]` 标签**，来源群 = thread_id 的 guild 段）。
- **配置**（env `BOT_RAG_ENABLED` / `BOT_EMBED_MODEL` / `OLLAMA_BASE_URL` / `BOT_EMBED_DIMENSIONS` / `BOT_EMBED_CACHE_ENABLED` / `BOT_EMBED_CACHE_MAX_ENTRIES` / `BOT_RAG_TOP_K` / `BOT_RAG_SCORE_THRESHOLD` / `BOT_RAG_RETENTION_PER_THREAD` / `BOT_RAG_MAX_AGENT_ROUNDS`；视觉复用 `OLLAMA_BASE_URL`，env `BOT_VISION_ENABLED` / `BOT_VISION_MODEL` / `BOT_VISION_MAX_IMAGES` / `BOT_VISION_TIMEOUT`；主 LLM 多模态开关 `BOT_LLM_MULTIMODAL`，默认 0 = 图片走本地视觉，1 = 图片直接进主 LLM）。
- **MCP**（env `BOT_MCP_ENABLED` / `BOT_MCP_SERVERS` / `BOT_MCP_TOOL_NAME_PREFIX` / `TAVILY_API_KEY`；Tavily 走官方远程 streamable_http 端点 `https://mcp.tavily.com/mcp/?tavilyApiKey=...`）。env 解析在 `common/mcp.py::parse_mcp_servers`（`BotConfig` 的 `mcp_servers` validator），连接合并（额外 server + Tavily 自动注册）在 `bot/core/mcp/config.py::build_mcp_connections`，`main.py` 调用后交给 `load_mcp_tools`。加载到工具后 `call_llm` 按 `use_mcp`（`bool(mcp_tools)`）注入 `MCP_TOOL_HINT` 提示层，引导 LLM 在时效性/超知识范围问题上主动调用外部工具。

### 记忆工具（用户持久记忆）

- **触发**：`main.py` 构建 `MemoryStore` 并注入 `create_graph(...)` 后，`call_llm` 额外绑定 `remember_user_memory` / `recall_user_memory` 工具，并注入 `MEMORY_TOOL_HINT` 提示层，**LLM 自行决定何时保存/检索**。工具定义在 `bot/core/tools/user_memory.py`，执行由 prebuilt `ToolNode` 统一执行。
- **执行**：`tools（ToolNode）` 经 `InjectedState("user_id")` 注入 `user_id`（记忆按用户维度存取），`memory_store` 由 `build_tools` 闭包绑定注入；`remember_user_memory(key, value, ...)` / `recall_user_memory(keyword, ...)` 均为纯函数，底层经官方 `AsyncSqliteStore`（全 async）直接 `await`，无需 `to_thread` 包装。
- **不再进图前全量注入 / 图外抽取**：旧方案在 `call_llm` 前将全部用户记忆拼入 SystemMessage，并在图外由 `_extract_memories` 抽取持久化 —— 均已移除。记忆完全由 LLM 通过工具主动读写，`user_id` 从 state 传入。

### 技能模块（提示词包）

- **加载**：`main.py` 在 `config.skills_enabled`（默认开启，env `BOT_SKILLS_ENABLED`）时用 `SkillRegistry.from_directory(config.skills_dir, index_max=config.skills_index_max)` 扫描 `skills/<name>/SKILL.md`（env `BOT_SKILLS_DIR` 默认 `"skills"`、`BOT_SKILLS_INDEX_MAX` 默认 50），解析 frontmatter 的 `name`/`description` + 正文；目录不存在 → 空注册表，绝不崩 bot。注册表注入 `create_graph(skill_registry=...)`。
- **触发**：`build_tools` 在 `skill_registry` 非空时把 `load_skill` / `unload_skill` 包装为 `BaseTool`（纯函数在 `bot/core/skills/tools.py`，只返回正文/确认，不写任何状态）。`call_llm` 经 `build_system_messages` 注入**技能索引层**（`SKILL_INDEX_HINT` + `index_text()`）引导 LLM 按需 `load_skill` 取回正文。
- **激活写回**：工具执行由 prebuilt `ToolNode` 完成后，图边 `tools → skill_manager → call_llm` **逐轮**触发 `skill_manager` 节点（`bot/core/nodes/action_node/skill_manager.py`）——扫描最近带 tool_calls 的 AIMessage，把 `load_skill` 的 `skill_name` 追加进 `BotState.active_skills`（`unload_skill` 移除；**只增不改、不设 reducer**，last-write-wins）。必须逐轮接线而非图末一次：否则早期轮次的 load 调用会被后置轮次覆盖漏掉。
- **注入层**：激活后 `build_system_messages` 注入**激活正文层**（`SKILL_ACTIVE_HINT` + 各技能 body；技能被删则静默跳过）；`estimate_context_tokens` 复用同一函数保证 token 估算一致。`active_skills` 经 checkpoint 跨轮持久化、按 thread 隔离（跨线程不串技能）。
- **关键约束**：`active_skills` 是图状态通道，但 `bot/handler.py` **绝不注入**它——LangGraph 输入状态覆盖 checkpoint，每轮注入 `[]` 会清零已持久化的技能激活。节点一律用 `state.get("active_skills", [])` 读取。

### Reply is sent outside the graph

`MessageHandler.handle()` calls `SatoriApiClient.send_message()` after `graph.ainvoke()` returns. There is no `send_reply` node in the graph — the `reply_text` field flows through state and is consumed by the handler.

### Node type convention

When adding nodes, follow the classification in `bot/core/nodes/`:
- **`llm_node/`** — nodes that call an LLM for reasoning/generation (fixed position in graph)
- **`action_node/`** — deterministic, no-LLM logic nodes (fixed position in graph)；含 `skill_manager`（技能激活写回，挂在 tools 与 call_llm 之间的回环路径）
- **`tools` node** — prebuilt `langgraph.prebuilt.ToolNode`，统一执行全部工具（RAG 检索、用户记忆、技能 load/unload、MCP 外部工具），经条件边回环；工具列表由 `build_tools` 组装
- **`bot/core/mcp/`** — MCP 外部工具加载（`load_mcp_tools`），远程 streamable_http / stdio 多 server，单 server 失败降级跳过
- **`subgraph/`** — nested CompiledStateGraph for complex multi-step sub-flows

## Gotchas

- **`object/` package**: setuptools `__legacy__` backend renames `data_object` → `object` in editable installs. Always import from `object.*`, never `data_object.*`.

- **@-mention format**: LLOneBot/Satori uses XML `<at id="QQ号" name="昵称"/>`, not `@name`。回复判定基于 `parse_mentions` 的**顶层提及集合** `{id: 昵称}`（引用/转发子树不计），`detect_intent` 以 `bot_id` 命中为主、`bot_name` 昵称兜底。LLM 输入 `to_llm_text` 把 at 渲染为 `@昵称(id)`（`<at type="all"/>`→`所有成员`、`here`→`在线成员`）；`llm_text` 由 handler 每轮必注入，detect_intent 直接消费（无兜底）。

- **Satori 元素适配（content_parser）**: `to_llm_text` 媒体→占位符、@→`@昵称(id)`/`所有成员`、链接→`标题 (url)`、其余标签（排版/引用/转发/emoji/sharp/注释）全剥保留内部文本；`clean_text` 剥全部标签含闭合与注释。标签剥离仍走 `_TAG_RE` 单一来源；`_AT_TAG_RE` 仅用于 at 的提取（`parse_mentions`）与渲染。

- **回复判定树（router 已架空，纯确定性）**: text/image 在私聊或群聊**顶层**@时回复（引用/转发内不计）；file/audio/video 永不回复（即使私聊）。群聊非@的**文本**仍入上下文并跳 `summarize`、只索引用户消息 1 条；群聊非@的**图片**直接 END（不入上下文、不索引）；**回复轮图片**走 describe_image → call_llm。图文混合按主类型（content_kind）判定。判定表单一来源为 `bot/core/utils/routing.py`（`decide_reply` / `keep_in_context` / `route_after_detect`），`decide_reply` 按 `mentions`（`{id: 昵称}`）以 id 命中为主、昵称兜底，不再子串匹配 raw_content；`detect_intent` 与 `graph._route_after_detect` 共同消费，不再需要手动同步。

- **视觉节点（describe_image）**: `graph._route_after_detect` 的 `should_respond` 分支先走 `describe_image`（`bot/core/nodes/action_node/describe_image.py`）再进 `call_llm`。**双模式**：`llm_multimodal=0`（默认，纯文本 LLM）把 HumanMessage 里的 `[图片]` 原位替换为 `[图片：描述]`（同 message id → 原位替换）并写 `vision_desc`；`llm_multimodal=1`（主 LLM 多模态）把图片下载成 data URL、原位替换为多模态 content 数组（`build_multimodal_content` 文本/图片块交错），本地视觉仅产 `vision_desc` 供 RAG 索引（理解归主 LLM）。主 LLM 的多模态响应 content 是块列表，`call_llm`/`summarize` 的 `reply_text`/`conversation_summary` **一律经 `context.py::content_to_text` 归一化为字符串**再落 state（text 块拼接、image_url → `[图片]`）——直接透传列表会在 `index_turn` 的 `.strip()` 崩溃；`build_system_messages` 对摘要同样归一化（防旧 checkpoint 残留列表）。文本轮 / `vision_service` 为 None 时 no-op（占位符保留）。`VisionService`（`bot/core/vision/service.py`）下载图片 → base64 → Ollama `POST /api/generate`，单张失败返回 `""` 不抛出（占位符保留）；模块级 `download_images_as_data_urls` 复用同一套 SSRF/体积校验把图片转 data URL 供多模态主 LLM 消费。`image_srcs` 由 handler 从 `parse_content` 附件提取注入初始 state。图片全部失败时节点返回 `{"vision_desc": ""}`，清空陈旧描述防跨轮污染 RAG 索引。摘要格式化（`context.py::format_messages_for_summary`）对多模态 content 只取 text 块、图片归一为 `[图片]`，绝不带 base64 进摘要。

- **`[图片：{desc}]` 变体双文件**: `describe_image.py` 的 `replace_placeholders` 构造、`index_turn.py` 的 RAG 索引各自从 `vision_desc` 本地拼装，互不解析对方输出，当前无静默错配风险；但分隔符（`：`）变更时需同时改两处，测试须覆盖变体字符串。

- **`uv` package manager**: PyPI mirror is `https://mirrors.aliyun.com/pypi/simple/`（pyproject `[[tool.uv.index]]`）。Python >=3.12.

- **`.env` secrets**: `BASE_URL` + `API_KEY` (not `GO_BASE_URL`/`GO_API_KEY`). `.env-template` is the documented schema.

- **`db/` directory**: auto-created on startup（`main.py` `os.makedirs`）；sqlite 库文件全部惰性重建——`checkpoint.sqlite`（AsyncSqliteSaver 初始化建表）、`memory.sqlite`（`MemoryStore` 惰性建连，官方 `AsyncSqliteStore` `setup()` 建 `store` 表 + 自动迁移旧 `user_memories` 表）、`embed_cache.sqlite`（`CREATE TABLE IF NOT EXISTS`）；`milvus.db`（milvus-lite 单文件，位于 `db/milvus.db`，`MilvusStore` 首次启动自动建集合 `chat`）。删除任意库 → 下次启动重建；`checkpoint.sqlite` 含会话状态、`memory.sqlite` 含用户记忆，是真数据。`BotConfig.db_dir` (default `"db"`, env `BOT_DB_DIR`).

- **milvus-lite**: 群聊历史向量存 `db/milvus.db`（milvus-lite 单文件，raw pymilvus 直连）。集合 `chat` 双向量字段：`vector`（FLOAT_VECTOR，HNSW/COSINE，维度 = `embed_dimensions`）+ `sparse`（SPARSE_FLOAT_VECTOR，BM25）+ `text`（VARCHAR，jieba 分词 analyzer）+ `thread_id`（VARCHAR，partition key）；`BM25` 函数声明在 text 字段上（`input_field_names=["text"]`），sparse 输出字段须先声明再建 Function。`timestamp` 为 **TEXT ISO**（`YYYY-MM-DD HH:MM:SS`），淘汰按 `timestamp DESC`。每线程超过 `rag_retention_per_thread` 时由 `_prune_thread` 淘汰最旧记录（milvus-lite query 不支持 order_by → Python 侧排序切片删除；字符串/动态字段懒加载须先 `list()` 物化）。集合已存在时 `_ensure_collection` 先 `describe_collection` 校验 `vector` 字段 `params.dim`，与 `config.embed_dimensions` 不符则记 error 日志后 DROP 重建（对齐旧 sqlite-vec `_drop_legacy_schema` 先例：维度漂移的向量 insert 必失败，是可重建缓存直接丢弃）；**无论新建/复用，`_ensure_collection` 末尾统一 `load_collection`**——带索引集合（含 sparse/BM25/双索引）在新进程/重启后默认 `released`（load 状态是进程内 server 内存态，不持久化），query/search 前必须 load；跨进程打开 `db/milvus.db` 的独立脚本（如 `scripts/inspect_milvus.py`）也须显式 load 才能查询。搜索 hit 的动态字段（content/timestamp/sender 等）在新进程场景返回在 `entity` 子字典里（同进程为扁平 dict），`_dense_hit`/`_sparse_hit` 统一展平兼容。全新库自动建集合；全部操作由 RagService try/except 包裹降级，失败不崩图。**pymilvus 客户端硬编码激进 keepalive**（`grpc.keepalive_time_ms=10000` + `keepalive_permit_without_calls=True`，空闲每 10s 发 ping），而 milvus-lite server 用 gRPC 默认 ping 策略（无数据间隔 5 分钟、2 次违规即 GOAWAY）——bot 回合间空闲 45-120s 会触发 `too_many_pings` GOAWAY 反复掐断连接（每轮刷屏 + 有 RPC 被静默丢的风险）。`MilvusStore` 已用 `grpc_options=_MILVUS_GRPC_OPTIONS` 覆盖为不主动 ping + 5 分钟间隔；新增任何 pymilvus 直连客户端时须同样覆盖（`scripts/inspect_milvus.py` 短会话不受影响）。

- **工具定位**: RAG 工具纯函数在 `bot/core/tools/search_chat_history.py`，记忆工具纯函数在 `bot/core/tools/user_memory.py`，技能工具纯函数在 `bot/core/skills/tools.py`；`build_tools`（`bot/core/tools/factory.py`）把它们包装为 `BaseTool`（服务闭包绑定、`InjectedState` 注入 `thread_id`/`user_id`、异常降级为「工具执行失败。」）；执行节点为 prebuilt `ToolNode`。MCP 外部工具由 `bot/core/mcp/client.py::load_mcp_tools` 加载（每次调用自建 session）。`ToolNode` 绑定 `handle_tool_errors=_tool_error_message` 回调：只按类名记日志（防 Tavily URL 泄漏）、把执行/传输层异常统一降级为「工具执行失败。」不让异常中断整轮；**唯一例外**是 `ToolInvocationError`（参数校验失败）——原样返回 `exc.message` 逐字段校验信息供 LLM 自我纠正畸形参数。`MemoryStore` 数据文件仍为 `db/memory.sqlite`（官方 `store` 表，namespace `("user", user_id)`；首次启动自动把旧 `user_memories` 表迁移进 `store` 后 DROP）。工具调用消息会持久化到 checkpoint（不同于 SystemMessage）。

- **Persona fallback**: `main.py` uses `config.persona_prompt.strip() or DEFAULT_PERSONA_PROMPT` — the `BotConfig.persona_prompt` default is a real prompt string, not empty. Set `BOT_PERSONA_PROMPT=""` to force fallback to `DEFAULT_PERSONA_PROMPT`. Both use `{bot_name}` placeholder, formatted at invocation time in `call_llm_node`.
