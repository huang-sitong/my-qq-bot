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
main.py                      # entrypoint — wires BotConfig, LLM, Graph, Handler
common/                      # shared config + prompts (single source of truth)
  config.py                  #   BotConfig dataclass (env-var overrides)
  prompts.py                 #   DEFAULT_PERSONA_PROMPT, ROUTER_PROMPT, EXTRACT_PROMPT
bot/
  transport/websocket/       # Satori WS events: connect, identify, reconnect
  transport/http/            # Satori HTTP API: send_message, generic call_api
  core/
    graph.py                 # LangGraph assembly: creates (graph, checkpointer)
    llm.py                   # ChatOpenAI factory (reads BASE_URL / API_KEY from .env)
    memory.py                # MemoryStore — SQLite kv per user (memory.sqlite)
    nodes/                   # Graph nodes classified by execution mechanism:
      llm_node/              #   router, call_llm — invoke an LLM
      action_node/           #   load_context — deterministic logic, no LLM
      tool_node/             #   tools invoked by LLM via function calling (future)
      subgraph/              #   nested subgraphs (future)
    tools/                   # Tool definitions imported by graph / tool_node / subgraph
  handler.py                 # MessageHandler — ingress: routing, cooldown → graph → reply
object/                      # protocol data-objects (lazy-load via __getattr__)
  bot/state.py               #   BotState TypedDict (graph state schema)
  satori/                    #   Satori protocol: enums, models, events, API endpoints
db/                          # runtime databases (checkpoint.sqlite, memory.sqlite)
```

### Data flow

```
WebSocket event → SatoriClient → MessageHandler.handle()
  → fast-path routing (@mention / DM detection)
  → graph.ainvoke(state, thread_id)
    → router (llm_node)    ← LLM name-mention fallback for group chat
    → load_context (action_node)  ← inject persona + user memories
    → call_llm (llm_node)  ← generate reply
  → send reply via SatoriApiClient
  → extract memories via MemoryStore
```

### Two-database design

| File | Managed by | Purpose |
|---|---|---|
| `db/checkpoint.sqlite` | LangGraph `AsyncSqliteSaver` | Conversation state checkpoints (tables: `checkpoint`, `writes`, `user_memory`) |
| `db/memory.sqlite` | `MemoryStore` | Long-term user facts extracted by LLM (table: `user_memories`) |

### Session vs Thread

- **session_id** = `platform:guild:channel:user` — used for cooldowns and logging
- **thread_id** (checkpoint isolation):
  - Group chat → `platform:guild:channel` (shared conversation history)
  - Private chat → same as `session_id` (per-user history)

## Key patterns

### Lazy-loading `object/` package

`object/__init__.py` uses `__getattr__` + `_module_map` so that importing a single name (e.g. `from object.satori import EventBody`) doesn't load all sub-modules. When adding new Satori models or API params, update both the `__all__` list and `_module_map` in the corresponding `__init__.py`.

### Node dependency injection

Graph nodes in `bot/core/nodes/` use `functools.partial` for injection (not closures). In `graph.py`, `router_node` and `call_llm_node` are bound with `partial(node_fn, llm=llm)`, while `load_context` takes no injected dependencies. Each node file is a standalone `async def(state, ...) -> dict`.

### `create_graph()` returns a tuple

`create_graph()` returns `(graph: CompiledStateGraph, checkpointer: AsyncSqliteSaver)`. The `main.py` caller manages the checkpointer lifecycle — do not close it inside `create_graph`.

### Reply is sent outside the graph

`MessageHandler.handle()` calls `SatoriApiClient.send_message()` after `graph.ainvoke()` returns. There is no `send_reply` node in the graph — the `reply_text` field flows through state and is consumed by the handler.

### Node type convention

When adding nodes, follow the classification in `bot/core/nodes/`:
- **`llm_node/`** — nodes that call an LLM for reasoning/generation (fixed position in graph)
- **`action_node/`** — deterministic, no-LLM logic nodes (fixed position in graph)
- **`tool_node/`** — nodes invoked by LLM via function calling (LLM decides when)
- **`subgraph/`** — nested CompiledStateGraph for complex multi-step sub-flows

## Gotchas

- **`object/` package**: setuptools `__legacy__` backend renames `data_object` → `object` in editable installs. Always import from `object.*`, never `data_object.*`.

- **@-mention format**: LLOneBot/Satori uses XML `<at id="QQ号" name="昵称"/>`, not `@name`. Detection uses `f'<at id="{bot_id}"' in content`.

- **`uv` package manager**: PyPI mirror is `https://pypi.tuna.tsinghua.edu.cn/simple`. Python >=3.12.

- **`.env` secrets**: `BASE_URL` + `API_KEY` (not `GO_BASE_URL`/`GO_API_KEY`). `.env-template` is the documented schema.

- **`db/` directory**: auto-created on startup. Old `bot_memory.sqlite*` at project root is auto-migrated on launch. `BotConfig.db_dir` (default `"db"`, env `BOT_DB_DIR`).

- **Persona fallback**: `main.py` uses `config.persona_prompt.strip() or DEFAULT_PERSONA_PROMPT` — the `BotConfig.persona_prompt` default is a real prompt string, not empty. Set `BOT_PERSONA_PROMPT=""` to force fallback to `DEFAULT_PERSONA_PROMPT`.
