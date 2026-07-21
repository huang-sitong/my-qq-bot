# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

- Run bot: `uv run python main.py`
- Install/sync dependencies: `uv sync`
- Quick Python check: `uv run python -c "..."`

## Project Overview

QQ bot based on Satori protocol WebSocket + LangGraph + ChatOpenAI (OpenCode AI / DeepSeek V4 Flash).
Connects to QQ via LLOneBot's Satori protocol gateway.

## Architecture (3 layers)

```
bot/ws/      — WebSocket layer: SatoriClient connects to LLOneBot, receives events, sends HTTP API calls
bot/handler.py — Orchestration: @-mention detection → session isolation → cooldown → load memories → invoke graph
bot/agent/   — LangGraph layer: conversational state machine + LLM calls + MemoryStore
```

### `bot/ws/` — WebSocket + Satori Protocol
- `SatoriClient` connects to `ws://localhost:5600/v1/events`, sends IDENTIFY (op 3), handles events (op 0), pings (op 1/2), login (op 4)
- HTTP API at `http://localhost:5600` with Satori-Platform / Satori-User-ID / Authorization headers
- Auto-reconnect with exponential backoff + jitter

### `bot/handler.py` — Message Routing
- `handle_login()` stores bot_id for @-detection, sets Satori-User-ID on client config
- `handle()` checks @-mention (Satori XML `<at id="..."`), builds session_id (`platform:guild:channel:user`), enforces 3s cooldown, strips mention tag, loads user memories, invokes graph, then extracts memories
- `_extract_memories()` — separate LLM call to extract user facts from conversation, stored via MemoryStore

### `bot/agent/` — LangGraph + Memory
- `BotState` (TypedDict): messages, persona, user_memories, session_id, new_message, reply_text, guild_id, channel_id
- `create_graph()` → 3-node `StateGraph`: `load_context` (inject persona + memories) → `call_llm` (ChatOpenAI) → `send_reply` (Satori HTTP API)
- Checkpointer: `AsyncSqliteSaver` with `aiosqlite` → persists full message history to `bot_memory.sqlite`
- Session isolation via `thread_id`

### Cross-Session Memory
- `MemoryStore` — SQLite `user_memories` table, kv by user_id (cross-guild)
- Memories loaded before graph invoke, injected into system prompt
- After reply sent, a separate LLM call extracts new facts and upserts them

## Key Technical Details

- **@mention format**: LLOneBot/Satori uses XML `<at id="QQ号" name="昵称"/>`, NOT `@昵称` — detection checks `f'<at id="{bot_id}"'` in content (handler.py:128)
- **Session ID**: `f"{platform}:{guild_id}:{channel_id}:{user_id}"` — used as LangGraph `thread_id` for checkpoint isolation
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
