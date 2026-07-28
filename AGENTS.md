# AGENTS.md

## Commands

```
uv sync              # install dependencies
uv run python main.py   # run the bot
uv run python -c "..."  # quick check
```

## Architecture

```
main.py                   # bot/satori entrypoint
bot/
  transport/websocket/    # WS: receive events, reconnect
  transport/http/         # HTTP: Satori API calls
  core/                   # LangGraph pipeline
    graph.py              #   assembles graph, returns (graph, checkpointer)
    nodes/                #   router → load_context → call_llm
    memory.py             #   MemoryStore (SQLite kv per user)
    llm.py                #   ChatOpenAI factory (BASE_URL / API_KEY from .env)
    persona.py            #   loads [tool.bot].persona_prompt from pyproject.toml
  handler.py              #   orchestrator: route → graph → send reply → extract memories
object/                   #   data objects (setuptools legacy backend renames data_object→object)
  bot/                    #     BotState, BotConfig
  satori/                 #     Satori protocol models, events, API params
db/                       #   runtime databases (checkpoint.sqlite, memory.sqlite)
```

## Gotchas

- **`object/` package**: setuptools `__legacy__` backend renames `data_object` to `object` in editable installs. Always import from `object.*`, never `data_object.*`. Do not rename this directory back.

- **`create_graph()` returns a tuple**: `(graph: CompiledStateGraph, checkpointer: AsyncSqliteSaver)`, not just the graph. Caller manages checkpointer lifecycle.

- **Reply is sent outside the graph**: `handler.py` calls `SatoriApiClient.send_message()` after `graph.ainvoke()` returns. The graph no longer has a `send_reply` node.

- **Node dependency injection**: uses `functools.partial` in `graph.py`, not closures. Each node in `nodes/` is a standalone `async def(state, llm) -> dict`.

- **@-mention format**: Satori/LLOneBot uses XML `<at id="QQ号" name="昵称"/>`, not `@name`. Detection uses `f'<at id="{bot_id}"' in content`.

- **Session vs Thread**: `session_id = platform:guild:channel:user` (cooldown, logging). `thread_id` (checkpoint isolation): group chat → `platform:guild:channel` (shared history), private chat → full session_id.

- **db/ directory**: auto-created on startup. Old `bot_memory.sqlite*` from root is auto-migrated. `BotConfig.db_dir` (default `"db"`, env `BOT_DB_DIR`).

- **`uv` package manager**: PyPI mirror is `https://pypi.tuna.tsinghua.edu.cn/simple`. Python >=3.12.
