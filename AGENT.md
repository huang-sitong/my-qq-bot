# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

- Run bot: `uv run python main.py`
- Install/sync dependencies: `uv sync`
- Quick Python check: `uv run python -c "..."`

## Project Overview

QQ bot based on Satori protocol WebSocket + LangGraph + ChatOpenAI (OpenCode AI / DeepSeek V4 Flash).
Connects to QQ via LLOneBot's Satori protocol gateway.

## Architecture (3 modules)

```
main.py                          # Entry point, assembly
  │
  ├── transport/websocket/       # Module 1: WebSocket receive
  │   └── SatoriClient           #   WS connection, event dispatch, reconnect
  │
  ├── transport/http/            # Module 3: HTTP API send
  │   └── SatoriApiClient        #   Satori HTTP API calls (send_message, etc.)
  │
  ├── core/                      # Module 2: BotCore processing
  │   ├── graph.py               #   StateGraph assembly (functools.partial)
  │   ├── nodes/                 #   Independent node functions
  │   │   ├── router.py          #     router_node(state, llm)
  │   │   ├── load_context.py    #     load_context(state)
  │   │   └── call_llm.py        #     call_llm_node(state, llm)
  │   ├── prompts.py             #   ROUTER_PROMPT, EXTRACT_PROMPT
  │   ├── llm.py                 #   ChatOpenAI factory (setup_llm)
  │   ├── memory.py              #   MemoryStore (SQLite, cross-session)
  │   └── persona.py             #   Persona loading from pyproject.toml
  │
  ├── handler.py                 # Orchestrator: receive → invoke core → send
  │
  └── data_object/               # Data objects (unified)
      ├── satori/                #   Satori protocol objects
      │   ├── enums.py           #     ChannelType, Direction, LoginStatus, Order
      │   ├── models.py          #     User, Guild, Message, Channel, etc.
      │   ├── events.py          #     EventBody, LoginList, Signal
      │   └── api.py             #     Endpoint, params, MESSAGE_CREATE, etc.
      └── bot/                   #   Bot internal objects
          ├── state.py           #     BotState (TypedDict)
          └── config.py          #     BotConfig (dataclass)

data/                            # Database files
  ├── checkpoint.sqlite          #   LangGraph checkpoint (conversation history)
  ├── memory.sqlite              #   user_memories table (cross-session facts)
  └── rag.sqlite                 #   Reserved for future RAG
```

### `bot/transport/websocket/` — WebSocket Layer
- `SatoriClient` connects to `ws://localhost:5600/v1/events`, sends IDENTIFY (op 3), handles events (op 0), pings (op 1/2), login (op 4)
- Auto-reconnect with exponential backoff + jitter
- Pure WebSocket — no HTTP API calls

### `bot/transport/http/` — HTTP API Layer
- `SatoriApiClient` manages `httpx.AsyncClient` lifecycle
- `call_api(endpoint, params)` — generic Satori HTTP API call
- `send_message(channel_id, content)` — convenience for message sending
- Sets Satori-Platform / Satori-User-ID / Authorization headers

### `bot/handler.py` — Message Routing
- `handle_login()` stores bot_id for @-detection and name, sets Satori-User-ID on config
- `handle()` routing logic:
  - **Private chat** (`ChannelType.DIRECT`) → always respond
  - **Group chat + @-mention** → respond
  - **Group chat, no @-mention** → set `should_respond=False`, let graph's router node decide via LLM name-mention detection
- Enforces 3s per-user cooldown, strips @-mention XML tag, loads user memories, builds HumanMessage with `name` field for group chats, invokes graph
- After graph returns, calls `_send_reply()` → `SatoriApiClient.send_message()`
- `_extract_memories()` — separate LLM call to extract user facts, stored via MemoryStore

### `bot/core/` — BotCore (LangGraph State Machine)
- **Graph structure** (`graph.py`):
  ```
  START → router → (conditional)
                    ├─ True  → load_context → call_llm → END
                    └─ False → END
  ```
  - **router** (`nodes/router.py`): if `should_respond=False`, calls LLM with `ROUTER_PROMPT` to detect name mention; sets `should_respond`
  - **load_context** (`nodes/load_context.py`): inject persona + user memories as SystemMessage, append new_message
  - **call_llm** (`nodes/call_llm.py`): ChatOpenAI call, handles timeout with fallback reply
- Nodes use `functools.partial` for dependency injection (no closures)
- Returns `(graph, checkpointer)` tuple for external lifecycle management
- Checkpointer: `AsyncSqliteSaver` with `aiosqlite` → `data/checkpoint.sqlite`

### Cross-Session Memory
- `MemoryStore` — SQLite `user_memories` table, kv by user_id (cross-guild)
- Memories loaded before graph invoke, injected into system prompt
- After reply sent, a separate LLM call extracts new facts and upserts them

## Key Technical Details

- **@mention format**: LLOneBot/Satori uses XML `<at id="QQ号" name="昵称"/>`, NOT `@昵称` — detection checks `f'<at id="{bot_id}"'` in content (handler.py)
- **Session ID**: `f"{platform}:{guild_id}:{channel_id}:{user_id}"` — used for cooldown + logging
- **Thread ID** (checkpoint isolation): group chat → `platform:guild:channel` (shared history), private chat → full session ID (per-user)
- **Router agent**: first node in LangGraph; for group chat without @-mention, calls LLM to detect bot name mention in message text; LLM failure defaults to no response
- **Group chat user identity**: `HumanMessage(content=..., name=user_nick)` distinguishes senders in shared checkpoint history
- **LLM**: ChatOpenAI → OpenCode AI (deepseek-v4-flash), configured via `.env` (`GO_BASE_URL`, `GO_API_KEY`)
- **Persona**: loaded from `pyproject.toml` `[tool.bot].persona_prompt`, cached with `@lru_cache`
- **Reply sending**: moved out of LangGraph — handler calls `SatoriApiClient.send_message()` after graph returns

## Configuration

| Setting | Location |
|---------|----------|
| LLM base URL + API key | `.env` (GO_BASE_URL, GO_API_KEY) |
| WebSocket / HTTP API URL | `data_object/bot/config.py` BotConfig |
| Database directory | `BotConfig.db_dir` (default `"data"`, env `BOT_DB_DIR`) |
| Bot persona | `pyproject.toml` [tool.bot].persona_prompt |
| Satori token | `BotConfig.token` (set via code) |
| Platform flag | `BotConfig.api_platform = "llonebot"` |

## Database

`data/` directory (auto-created on startup) stores:
1. `checkpoint.sqlite` — LangGraph checkpoint data (full conversation history, per thread_id)
2. `memory.sqlite` — `user_memories` table (cross-session user facts)
3. `rag.sqlite` — reserved for future RAG (SQLite-vec)

Old root-level `bot_memory.sqlite*` files are auto-migrated to `data/` on startup.
`data/*.sqlite*` is gitignored (except `.gitkeep`).

## Backward Compatibility

Old import paths still work via re-exports:
- `bot.ws.*` → `bot.transport.websocket.*`
- `bot.agent.graph` → `bot.core.graph`
- `bot.agent.llm` → `bot.core.llm`
- `bot.agent.memory` → `bot.core.memory`
- `bot.agent.persona` → `bot.core.persona`
- `bot.agent.graph.BotState` → `data_object.bot.state.BotState`
- `bot.ws.config.BotConfig` → `data_object.bot.config.BotConfig`

## Package Management

Uses `uv` (not pip). Dependencies in `pyproject.toml` under `[project]dependencies`.
PyPI mirror: `https://pypi.tuna.tsinghua.edu.cn/simple`.
Python 3.12+.
