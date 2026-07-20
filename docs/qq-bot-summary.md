# QQ 机器人 — 实施总结

## 概述

基于 Satori 协议 WebSocket + LangGraph + DeepSeek V4 Flash 的通用 AI 聊天助手，接入 QQ（通过 LLOneBot）。

## 架构

```
QQ 消息 → LLOneBot → WebSocket(Satori) → SatoriClient
                                         → MessageHandler.handle()
                                            → @检测（Satori <at> 标签）
                                            → session 隔离
                                            → LangGraph.ainvoke()
                                               → load_context (注入人格)
                                               → call_llm (ChatOpenAI → OpenCode AI)
                                               → send_reply (Satori HTTP API)
                                            → QQ 群回复
```

## 文件结构

```
main.py                       # 入口
pyproject.toml                # 项目配置 + [tool.bot] persona_prompt
bot/
├── __init__.py               # 导出
├── config.py                 # BotConfig（WebSocket/API 连接配置）
├── client.py                 # SatoriClient（WebSocket + HTTP API）
├── llm.py                    # setup_llm() → ChatOpenAI
├── persona.py                # load_persona() → 从 pyproject.toml 加载
├── graph.py                  # LangGraph: BotState + 3节点 + AsyncSqliteSaver
└── handler.py                # MessageHandler: @检测 + session隔离 + 冷却
```

## 配置

| 配置项 | 位置 | 说明 |
|--------|------|------|
| WebSocket URL | `bot/config.py` | `ws://localhost:5600/v1/events` |
| API Base URL | `bot/config.py` | `http://localhost:5600` |
| LLM Base URL | `.env` | `GO_BASE_URL` |
| API Key | `.env` | `GO_API_KEY` |
| 模型 | `bot/llm.py` | `deepseek-v4-flash` |
| 人格提示词 | `pyproject.toml` | `[tool.bot].persona_prompt` |

## 关键技术决策

- **@检测**: LLOneBot 使用 Satori 协议，@格式为 `<at id="QQ号" name="昵称"/>`，非 `@昵称`
- **检查点**: `AsyncSqliteSaver` + `aiosqlite`（`SqliteSaver` 不支持 async）
- **Session 隔离**: `thread_id = platform:guild_id:channel_id:user_id`
- **冷却**: 3 秒内存字典，按 session_id

## 运行

```bash
uv run python main.py
```

要求 LLOneBot 运行、启用 Satori 协议（默认端口 5600）。

## 数据库

`bot_memory.sqlite`（含 `-shm`、`-wal` WAL 文件）为运行时自动生成，已加入 `.gitignore`。
