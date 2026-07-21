# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

- Run bot: `uv run python main.py`
- Install/sync dependencies: `uv sync`
- Quick Python check: `uv run python -c "..."`

## Project Overview

QQ bot based on Satori protocol WebSocket + LangGraph + ChatOpenAI (OpenCode AI / DeepSeek V4 Flash).
Connects to QQ via LLOneBot's Satori protocol gateway.

## Architecture (4 layers)

```
bot/ws/      — WebSocket layer: SatoriClient connects to LLOneBot, receives events, sends HTTP API calls
bot/handler.py — Orchestration: routing → cooldown → session isolation → load memories → invoke graph
bot/agent/graph.py — LangGraph state machine: router → context → LLM → reply
bot/agent/memory.py — Long-term memory: MemoryStore (SQLite, cross-session)
```

### `bot/ws/` — WebSocket + Satori Protocol
- `SatoriClient` connects to `ws://localhost:5600/v1/events`, sends IDENTIFY (op 3), handles events (op 0), pings (op 1/2), login (op 4)
- HTTP API at `http://localhost:5600` with Satori-Platform / Satori-User-ID / Authorization headers
- Auto-reconnect with exponential backoff + jitter

### `bot/handler.py` — Message Routing
- `handle_login()` stores bot_id for @-detection and name, sets Satori-User-ID on client config
- `handle()` routing logic:
  - **Private chat** (`ChannelType.DIRECT`) → always respond
  - **Group chat + @-mention** → respond
  - **Group chat, no @-mention** → set `should_respond=False`, let graph's router node decide via LLM name-mention detection
- Enforces 3s per-user cooldown, strips @-mention XML tag, loads user memories, builds HumanMessage with `name` field for group chats, invokes graph, then extracts memories
- `_extract_memories()` — separate LLM call to extract user facts from conversation, stored via MemoryStore

### `bot/agent/graph.py` — LangGraph State Machine
- `BotState` (TypedDict): messages, persona, user_memories, session_id, new_message, reply_text, guild_id, channel_id, should_respond, bot_name
- `create_graph()` → 4-node `StateGraph`:
  ```
  START → router → (conditional)
                    ├─ True  → load_context → call_llm → send_reply → END
                    └─ False → END
  ```
  - **router**: if `should_respond=False`, calls LLM with `ROUTER_PROMPT` to detect name mention in group chat; sets `should_respond`
  - **load_context**: inject persona + user memories as SystemMessage, append new_message
  - **call_llm**: ChatOpenAI call, handles timeout with fallback reply
  - **send_reply**: Satori HTTP API `MESSAGE_CREATE`
- Checkpointer: `AsyncSqliteSaver` with `aiosqlite` → persists full message history to `bot_memory.sqlite`
- Session isolation via `thread_id`

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

## Configuration

| Setting | Location |
|---------|----------|
| LLM base URL + API key | `.env` (GO_BASE_URL, GO_API_KEY) |
| WebSocket / HTTP API URL | `bot/ws/config.py` BotConfig |
| Bot persona | `pyproject.toml` [tool.bot].persona_prompt |
| Satori token | `BotConfig.token` (set via code) |
| Platform flag | `BotConfig.api_platform = "llonebot"` |

## Database

`bot_memory.sqlite` (auto-created) stores two things:
1. LangGraph checkpoint data (full conversation history, per thread_id)
2. `user_memories` table (cross-session user facts)

Managed files (.sqlite-shm, .sqlite-wal) are gitignored via `bot_memory.sqlite*`.

## Package Management

Uses `uv` (not pip). Dependencies in `pyproject.toml` under `[project]dependencies`.
PyPI mirror: `https://pypi.tuna.tsinghua.edu.cn/simple`.
Python 3.12+.
