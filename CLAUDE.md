# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```
uv sync                  # install dependencies
uv run python main.py    # run the bot
uv run python -c "..."   # quick import / logic check
```

## Architecture

```
main.py                      # entrypoint — wires BotConfig, LLM, Graph, Handler, RagService, MemoryStore
common/                      # shared config + prompts (single source of truth)
  config.py                  #   BotConfig dataclass (env-var overrides)
  prompts.py                 #   DEFAULT_PERSONA_PROMPT, ROUTER_PROMPT, SUMMARY_PROMPT, MEMORY_TOOL_HINT
bot/
  transport/websocket/       # Satori WS events: connect, identify, reconnect
  transport/http/            # Satori HTTP API: send_message, generic call_api
  core/
    graph.py                 # LangGraph assembly: creates (graph, checkpointer)
    llm.py                   # ChatOpenAI factory (reads BASE_URL / API_KEY from .env)
    memory.py                # MemoryStore — SQLite kv per user (memory.sqlite)
    rag/                     # 群聊历史 RAG（向量检索）
      embedder.py            #   EmbeddingService — Ollama qwen3-embedding，Instruct 前缀
      service.py             #   RagService — index_turn / search 组合接口
      store.py               #   RagVectorStore — sqlite-vec 向量表 + 元数据表 (rag.sqlite)
    utils/                   # Pure utility functions (no state)
      context.py             #   token estimation + message formatting for summarization
      content_parser.py      #   Satori content 解析：消息类型分类 + 附件 + 清洗文本（clean_text / to_llm_text）
    nodes/                   # Graph nodes classified by execution mechanism:
      llm_node/              #   router, call_llm — invoke an LLM
      action_node/           #   detect_intent (routing), summarize (context window management), index_turn (RAG 入库)
      tool_node/             #   tool_node — 执行 LLM 请求的工具调用（图级循环）
      subgraph/              #   nested subgraphs (future)
    tools/                   # Tool definitions imported by graph / tool_node / subgraph
      search_chat_history.py #   search_chat_history 工具（TOOL_SCHEMA + 纯函数）
      user_memory.py         #   remember_user_memory / recall_user_memory 工具（TOOL_SCHEMA + 纯函数）
  handler.py                 # MessageHandler — ingress: validation → queue → graph → reply → RAG index
object/                      # protocol data-objects (lazy-load via __getattr__)
  bot/state.py               #   BotState TypedDict (graph state schema)
  satori/                    #   Satori protocol: enums, models, events, API endpoints
db/                          # runtime databases (checkpoint.sqlite, memory.sqlite, rag.sqlite)
```

### Data flow

```
WebSocket event → SatoriClient → MessageHandler.handle()
  → validation + enqueue → worker dequeues（按 thread_id 加锁串行化）
  → graph.ainvoke(state, thread_id)
    → detect_intent (action_node)  ← DIRECT / @-mention → should_respond
    → router (llm_node)            ← LLM name-mention fallback
    → call_llm (llm_node)          ← dynamic SystemMessage injection
      tool_node (tool_node)    ← call_llm 返回 tool_calls 时按工具名分发（search_chat_history / remember_user_memory / recall_user_memory），回环到 call_llm
    → summarize (action_node)      ← token threshold check → progressive summary
    → index_turn (action_node)     ← 有回复的对话写入 RAG 向量库（用户消息 + Bot 回复）
  → send reply via SatoriApiClient
```

### Three-database design

| File | Managed by | Purpose |
|---|---|---|
| `db/checkpoint.sqlite` | LangGraph `AsyncSqliteSaver` | Conversation state checkpoints (tables: `checkpoint`, `writes`, `user_memory`) |
| `db/memory.sqlite` | `MemoryStore` | Long-term user facts written by LLM via remember/recall tools (table: `user_memories`) |
| `db/rag.sqlite` | `RagVectorStore` | Group chat history vectors for semantic retrieval (`chat_embeddings` vec0 table + `chat_embedding_meta`) |

### Session vs Thread

- **session_id** = `platform:guild:channel:user` — used for logging
- **thread_id** (checkpoint isolation):
  - All chats → `platform:guild:channel` (per-channel conversation history, guild-reserved for multi-platform)

## Key patterns

### Lazy-loading `object/` package

`object/__init__.py` uses `__getattr__` + `_module_map` so that importing a single name (e.g. `from object.satori import EventBody`) doesn't load all sub-modules. When adding new Satori models or API params, update both the `__all__` list and `_module_map` in the corresponding `__init__.py`.

### Node dependency injection

Graph nodes in `bot/core/nodes/` use `functools.partial` for injection (not closures). In `graph.py`:
- `router_node` bound with `partial(router_node, llm=llm)`
- `call_llm_node` bound with `partial(call_llm_node, llm=llm, rag_service=rag_service, memory_store=memory_store, bot_config=config)`
- `summarize_node` bound with `partial(summarize_node, llm=llm, bot_config=config)`
- `tool_node` bound with `partial(tool_node, rag_service=rag_service, memory_store=memory_store)`

Each node file is a standalone `async def(state, ...) -> dict`.

### `create_graph()` returns a tuple

`create_graph(llm, config, db_dir="db", rag_service=None, memory_store=None)` returns `(graph: CompiledStateGraph, checkpointer: AsyncSqliteSaver)`. The `main.py` caller manages the checkpointer lifecycle — do not close it inside `create_graph`. `rag_service` / `memory_store` are passed to `call_llm_node` (tool binding) and `tool_node` (tool execution) for the graph-level tool loop.

### SystemMessage injection

`call_llm_node` builds a **three-layer** SystemMessage list **dynamically each invocation**, prepends it to `state["messages"]`, and returns only the `AIMessage` to state:

```python
# Persona is formatted with bot_name for self-awareness
persona = state["persona"].format(bot_name=state.get("bot_name", ""))
system_msgs = [SystemMessage(content=persona)]
# Layer 1: conversation_summary (from summarize_node)
# Layer 2: memory tools usage hint (MEMORY_TOOL_HINT, 仅注入 memory_store 时)
```

- SystemMessages are **local variables** — never persisted to checkpoint
- Persona is always at `messages[0]` regardless of conversation length — immune to context-window truncation
- Persona changes take effect immediately (no `has_persona` gate)
- Checkpoint stores conversation history (HumanMessage + AIMessage + ToolMessage)，not system instructions
- `DEFAULT_PERSONA_PROMPT` uses `{bot_name}` placeholder — formatted at invocation time, same pattern as `ROUTER_PROMPT`

### RAG（群聊历史检索）

- **触发**：`rag_enabled`（默认开启）。注入 `RagService` 后，`call_llm` 绑定 `search_chat_history` 工具（`memory_store` 注入时同时绑定 `remember_user_memory` / `recall_user_memory`），**LLM 自行决定何时检索**。若返回 `tool_calls`，条件边路由到 `tool_node` 执行，回边到 `call_llm` 继续；`tool_rounds`（总工具轮次计数）达到 `rag_max_agent_rounds` 后走无工具路径强制收尾。
- **索引**：每轮**有回复**的对话由图内 `index_turn` 节点（action_node，位于 summarize 与 END 之间）写入向量库（用户消息 + Bot 回复两条记录）；用户内容先经 `clean_text` 清洗（剥全部元素标签 + unescape），纯媒体消息跳过。索引失败仅降级（`RagService.index_turn` 内部吞异常）。
- **嵌入**：Ollama `qwen3-embedding`（`embedder.py`）。Query 与 Document **共用** `Instruct: 检索群聊历史中与问题最相关的消息` 前缀以保持向量空间一致 —— qwen3 是对话模板模型，检索必须加 Instruct 前缀（见 `test/test_ollama_embedding.py` 项 5）。
- **检索策略**（`store.search`）：取 `candidate_k=50` 候选 → 过滤 `score = 1 - cosine_distance ≥ score_threshold` → **当前群聊优先，本群命中不足时用跨群结果补齐**。
- **工具闭环**：`search_chat_history(query, rag_service, thread_id)` 是纯函数，`tool_node` 从 state 注入 `thread_id`，`rag_service` 由 `functools.partial` 绑定注入；工具调用消息（AIMessage + ToolMessage）持久化到 checkpoint。
- **配置**（env `BOT_RAG_ENABLED` / `BOT_EMBED_MODEL` / `OLLAMA_BASE_URL` / `BOT_EMBED_DIMENSIONS` / `BOT_RAG_TOP_K` / `BOT_RAG_SCORE_THRESHOLD` / `BOT_RAG_RETENTION_PER_THREAD` / `BOT_RAG_MAX_AGENT_ROUNDS`）。

### 记忆工具（用户持久记忆）

- **触发**：`main.py` 构建 `MemoryStore` 并注入 `create_graph(...)` 后，`call_llm` 额外绑定 `remember_user_memory` / `recall_user_memory` 工具，并注入 `MEMORY_TOOL_HINT` 提示层，**LLM 自行决定何时保存/检索**。工具定义在 `bot/core/tools/user_memory.py`，执行由通用 `tool_node` 按工具名分发。
- **执行**：`tool_node` 从 state 注入 `user_id`（记忆按用户维度存取），`memory_store` 由 `functools.partial` 绑定注入；`remember_user_memory(key, value, ...)` / `recall_user_memory(keyword, ...)` 均为纯函数，同步 sqlite 操作经 `asyncio.to_thread` 异步化。
- **不再进图前全量注入 / 图外抽取**：旧方案在 `call_llm` 前将全部用户记忆拼入 SystemMessage，并在图外由 `_extract_memories` 抽取持久化 —— 均已移除。记忆完全由 LLM 通过工具主动读写，`user_id` 从 state 传入。

### Reply is sent outside the graph

`MessageHandler.handle()` calls `SatoriApiClient.send_message()` after `graph.ainvoke()` returns. There is no `send_reply` node in the graph — the `reply_text` field flows through state and is consumed by the handler.

### Node type convention

When adding nodes, follow the classification in `bot/core/nodes/`:
- **`llm_node/`** — nodes that call an LLM for reasoning/generation (fixed position in graph)
- **`action_node/`** — deterministic, no-LLM logic nodes (fixed position in graph)
- **`tool_node/`** — tools invoked by LLM via function calling（`tool_node` 按工具名分发的通用工具节点：RAG 检索 + 用户记忆存取，经条件边回环）
- **`subgraph/`** — nested CompiledStateGraph for complex multi-step sub-flows

## Gotchas

- **`object/` package**: setuptools `__legacy__` backend renames `data_object` → `object` in editable installs. Always import from `object.*`, never `data_object.*`.

- **@-mention format**: LLOneBot/Satori uses XML `<at id="QQ号" name="昵称"/>`, not `@name`. Detection uses `f'<at id="{bot_id}"' in content`. Note there are **two** mention-strippers: `bot/handler.py:_strip_leading_mention` (RAG 索引前) and `bot/core/nodes/action_node/detect_intent.py:_strip_mention` (构造 HumanMessage 前).

- **`uv` package manager**: PyPI mirror is `https://pypi.tuna.tsinghua.edu.cn/simple`. Python >=3.12.

- **`.env` secrets**: `BASE_URL` + `API_KEY` (not `GO_BASE_URL`/`GO_API_KEY`). `.env-template` is the documented schema.

- **`db/` directory**: auto-created on startup. Old `bot_memory.sqlite*` at project root is auto-migrated on launch. `BotConfig.db_dir` (default `"db"`, env `BOT_DB_DIR`).

- **sqlite-vec**: `rag.sqlite` 需要 `sqlite_vec.load()` 扩展；`chat_embeddings` 是 `vec0` 虚拟表（cosine 距离，维度 = `embed_dimensions`），元数据按 `rowid` 关联普通表。每线程超过 `rag_retention_per_thread` 时按 `timestamp DESC` 淘汰最旧记录。

- **工具定位**: RAG 工具在 `bot/core/tools/search_chat_history.py`，记忆工具在 `bot/core/tools/user_memory.py`；执行节点为 `bot/core/nodes/tool_node/tool_node.py`，按工具名分发。`memory_store` 经 `functools.partial` 注入 `tool_node` 与 `call_llm`，`MemoryStore` 表仍为 `db/memory.sqlite`。工具调用消息会持久化到 checkpoint（不同于 SystemMessage）。

- **Persona fallback**: `main.py` uses `config.persona_prompt.strip() or DEFAULT_PERSONA_PROMPT` — the `BotConfig.persona_prompt` default is a real prompt string, not empty. Set `BOT_PERSONA_PROMPT=""` to force fallback to `DEFAULT_PERSONA_PROMPT`. Both use `{bot_name}` placeholder, formatted at invocation time in `call_llm_node`.
