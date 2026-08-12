# qq-bot

基于 Satori 协议的 QQ 聊天机器人：LangGraph 驱动的多轮对话，内置群聊历史 RAG 检索（milvus-lite）、用户持久记忆、图片视觉理解（本地 Ollama / 多模态主 LLM）与 MCP 外部工具。

## 快速开始

```bash
uv sync                       # 安装依赖
cp .env-template .env         # 填写 BASE_URL / API_KEY 等配置
uv run python main.py         # 启动 bot
```

## 配置

所有运行参数统一由 `common/config.py` 的 `BotConfig`（pydantic-settings）从 `.env` 读取，完整环境变量清单见 `.env-template`。核心项：

| 变量 | 说明 |
|---|---|
| `BASE_URL` / `API_KEY` | 主 LLM OpenAI 兼容端点 |
| `BOT_LLM_MODEL` | 主 LLM 模型名（默认 `deepseek-v4-flash`） |
| `BOT_LLM_MULTIMODAL` | `1` 时图片直接进主 LLM；`0` 走本地视觉（`BOT_VISION_MODEL`，默认 Ollama `qwen3-vl`） |
| `BOT_RAG_ENABLED` | 群聊历史向量检索（默认开启；嵌入用 Ollama `qwen3-embedding`） |
| `BOT_EMBED_BASE_URL` | 嵌入向量专用 Ollama 地址（默认本地；未设置时回落 `OLLAMA_BASE_URL`） |
| `BOT_VISION_BASE_URL` | 视觉模型专用 Ollama 地址（默认本地；未设置时回落 `OLLAMA_BASE_URL`） |
| `OLLAMA_BASE_URL` | 旧共用 Ollama 地址，作为嵌入/视觉的兼容回落 |
| `BOT_AUTO_REPLY_RANDOM_RATE` | auto_reply 非@消息的随机回复概率，默认 `0.3` |
| `BOT_AUTO_REPLY_COOLDOWN` | 同一会话两次 auto_reply 的最小间隔秒数，默认 `30` |
| `BOT_MCP_ENABLED` / `BOT_MCP_SERVERS_FILE` | MCP 外部工具（可选；server 定义在 `config/mcp_servers.json`） |

## 运行时数据

`db/` 目录（`BOT_DB_DIR` 可改）启动时自动创建，全部可删除重建（除用户真实记忆）：

- `checkpoint.sqlite` — LangGraph 会话状态
- `memory.sqlite` — 用户持久记忆（langgraph `AsyncSqliteStore`）
- `milvus.db` — 群聊历史向量（dense+sparse 混合检索）
- `embed_cache.sqlite` — 嵌入向量磁盘缓存

## 测试

```bash
uv run python -m pytest
```

## 架构

见 `CLAUDE.md`（入口 `main.py` → `MessageHandler` → LangGraph，节点分 `llm_node` / `action_node` / `tools` / `subgraph`，RAG 索引在图内 `index_turn` 节点）。
