# Tavily Web 搜索（websearch 功能）+ tool_rounds 预算规划

日期：2026-08-06
状态：已批准（2026-08-06）

## 背景

上次规划（`2026-08-05-mcp-toolnode-tavily-design.md`）已落地 MCP 基础设施 + Tavily 端点自动注册（提交 7cba445 / 658e26a 等）。本次是**增量**：让 websearch 成为一等公民——独立开关、LLM 提示层、工具循环预算规划。

现状核实：

- Tavily 官方远程端点 `https://mcp.tavily.com/mcp/?tavilyApiKey=<key>` 已在 `BotConfig.mcp_server_connections()` 自动注册（`tavily_api_key` 非空即注册）。
- 加载/执行/降级全链路已通：`load_mcp_tools` → `build_tools` → `ToolNode`，`_tool_error_message` 兜底（类名日志 + 「工具执行失败。」，`ToolInvocationError` 例外保留校验信息供 LLM 自纠）。
- 现有 3 个缺口：
  1. **LLM 无提示层**知道 `tavily_search` 的存在与使用时机，且可能把 `search_chat_history`（群聊历史）误当网络搜索。
  2. **Tavily 被 `BOT_MCP_ENABLED` 绑架**——开 Tavily 必须连带开放 `BOT_MCP_SERVERS` 里配置的任意外部 server，无安全边界。
  3. **`tool_rounds` 默认 3** 是 RAG/记忆时代的取值，websearch 加入后多步流（搜→extract、RAG+web 混合、畸形自纠）会顶到上限触发强制收尾。

## 决策（用户已确认）

1. **暴露全部 5 个 Tavily 工具**：`tavily_search` / `tavily_extract` / `tavily_map` / `tavily_crawl` / `tavily_research`，不做按名过滤（`load_mcp_tools` 逐 server 全量加载，现成支持）。
2. **独立 `websearch_enabled` 开关**（env `BOT_WEBSEARCH_ENABLED`，默认 `1`，与 `rag_enabled`/`vision_enabled` 一致），与 `mcp_enabled` 解耦，形成安全边界。
3. **新增 `WEB_SEARCH_HINT` 提示层**，`call_llm_node` 在 `use_websearch=True` 时注入（与 `MEMORY_TOOL_HINT` 模式对称）。
4. **`tool_rounds` 默认 3 → 4**；`recursion_limit = 2*max_rounds + 8`（handler.py:160）自动放宽，无需单独改。
5. **保留 `rag_max_agent_rounds` / `BOT_RAG_MAX_AGENT_ROUNDS` 字段与 env 名不动**（避免改用户 .env），CLAUDE.md 注明其为 RAG+记忆+web 共享工具循环预算。

## 文件变更

| 文件 | 动作 | 说明 |
|---|---|---|
| `common/config.py` | 改 | 新增 `websearch_enabled` 字段；`mcp_server_connections()` 门控语义调整 |
| `common/prompts.py` | 改 | 新增 `WEB_SEARCH_HINT` |
| `bot/core/nodes/llm_node/call_llm.py` | 改 | 新增 `use_websearch` 参数并注入 hint |
| `bot/core/graph.py` | 改 | 计算 `use_websearch` 传入 `call_llm_node` |
| `main.py` | 改 | MCP 加载条件加 `or config.websearch_enabled` |
| `.env-template` | 改 | 新增 `BOT_WEBSEARCH_ENABLED = 1` |
| `CLAUDE.md` | 改 | Web 搜索节 + tool_rounds 共享语义 + env 清单 |
| `tests/test_mcp_config.py` | 改 | 更新既有门控断言 + 新增 websearch_enabled 测试 |
| `tests/test_call_llm_node.py` | 改 | 新增 hint 注入测试 |
| `tests/test_graph.py` | 改 | 新增 MCP 工具图级回环测试 |

## 配置（common/config.py）

```python
# --- Web 搜索 (Tavily MCP, 独立于通用 MCP) ---
websearch_enabled: bool = field(
    default_factory=lambda: os.getenv("BOT_WEBSEARCH_ENABLED", "1") not in ("0", "false", "False", ""),
)

def mcp_server_connections(self) -> dict:
    """按启用开关返回应加载的 MCP server 连接配置。

    - mcp_enabled → 额外自定义 server（BOT_MCP_SERVERS）
    - websearch_enabled 且 TAVILY_API_KEY 非空 → Tavily 远程端点（自动注册）
    """
    servers = dict(self.mcp_servers) if self.mcp_enabled else {}
    if self.websearch_enabled:
        key = self.tavily_api_key.strip()
        if key:
            servers["tavily"] = {
                "transport": "streamable_http",
                "url": f"https://mcp.tavily.com/mcp/?tavilyApiKey={key}",
            }
    return servers
```

**语义变化**：原方法无条件并入 `mcp_servers`；新方法把额外 server 的并入交给 `mcp_enabled` 门控。该方法仅 main.py 与测试使用，无其他调用方（已 grep 核实）。

**安全边界**：`websearch_enabled=True` + key 时，即使 `mcp_enabled=False` 也只加载 Tavily；`BOT_MCP_SERVERS` 里配置的任意外部 server 不会被连带加载。

## main.py

```python
mcp_tools = []
if config.mcp_enabled or config.websearch_enabled:
    servers = config.mcp_server_connections()
    if servers:
        mcp_tools = await load_mcp_tools(
            servers, tool_name_prefix=config.mcp_tool_name_prefix,
        )
        logger.info("Loaded %d MCP tools", len(mcp_tools))
```

`mcp_tool_name_prefix` 维持默认 `0`（Tavily 工具名自带 `tavily_` 命名空间，与内部工具 `search_chat_history`/`remember_user_memory`/`recall_user_memory` 无冲突）。若用户开了 prefix 会得到 `tavily_tavily_search` 等丑陋名——可接受，文档注明。

## 提示层

`common/prompts.py`：

```python
WEB_SEARCH_HINT = """你可以通过 tavily_search 工具进行实时网络搜索（web 搜索）。
- 当用户询问实时/最新信息、新闻、天气、股价、赛事、外部网站内容，或群聊历史中不存在的客观事实时，
  调用 tavily_search 搜索，并在回答中列出来源 URL。
- search_chat_history 只能检索本群聊历史，不要用它查网络；tavily_search 检索整个互联网。
- 其他可用工具：tavily_extract（抓取指定 URL 正文）、tavily_map（地点/POI 搜索）、
  tavily_crawl（爬取站点）、tavily_research（深度研究）。默认先用 tavily_search；
  crawl/research 耗时长，仅在用户明确要求深度检索时使用。"""
```

`bot/core/nodes/llm_node/call_llm.py`：

```python
async def call_llm_node(
    state: BotState,
    llm: ChatOpenAI,
    tools: list[BaseTool] | None = None,
    use_memory: bool = False,
    use_websearch: bool = False,
    bot_config: BotConfig | None = None,
) -> dict:
    ...
    system_msgs = build_system_messages(persona, summary)
    if use_memory:
        system_msgs.append(SystemMessage(content=MEMORY_TOOL_HINT))
    if use_websearch:
        system_msgs.append(SystemMessage(content=WEB_SEARCH_HINT))
```

`bot/core/graph.py`：

```python
tools = build_tools(
    rag_service=rag_service, memory_store=memory_store, mcp_tools=mcp_tools,
)
use_memory = memory_store is not None
use_websearch = any(getattr(t, "name", "").startswith("tavily_") for t in tools)
builder.add_node(
    "call_llm", partial(
        call_llm_node, llm=llm, tools=tools,
        use_memory=use_memory, use_websearch=use_websearch, bot_config=config,
    )
)
```

以**工具实际存在**为信号（加载失败 → 无 tavily 工具 → 不注入，正确反映能力）。既有测试直接调用 `call_llm_node` 的用命名参数，新增参数带默认值，向后兼容。

## tool_rounds 预算规划

- **语义**：`tool_rounds` 在 handler.py:170 每条新消息显式重置为 `0`——**每消息硬上限，不跨轮累积**。1 轮 = LLM 一次带 `tool_calls` 的调用，一轮内可含**多个并行工具调用**（ToolNode 一次全部执行）。预算为 RAG 检索 + 记忆 + web 搜索共享。
- **默认 `rag_max_agent_rounds` 3 → 4**。

| 场景 | 轮数 |
|---|---|
| 简单查询（天气/新闻/股价） | 1 |
| 搜 → `tavily_extract` 抓正文 | 2 |
| 搜 → 换关键词再搜 | 2 |
| 群聊历史(RAG) 查不到 → 转 web 搜 | 2–3 |
| 畸形参数自纠（坏调用 + 重试） | 额外 1 |
| 深度研究链 | 3–4+ |

- **4 的理由**：3 是 RAG/记忆时代取值，websearch 加入后多步流会顶到上限触发无工具强制收尾；4 给出一轮缓冲覆盖「RAG+记忆+web 混合」「搜→extract→再搜」+ 一次自纠，同时不放任无限循环。最坏 4 轮 ≈ 40–60s，对聊天机器人可接受；多数查询并行调用只需 1–2 轮。
- **`recursion_limit = 2*max_rounds + 8`**（handler.py:160）自动放宽为 16，无需单独改。
- 超预算仍可通过 `BOT_RAG_MAX_AGENT_ROUNDS` env 上调（不新增字段）。

## 错误处理

沿用既有，无新改动：

| 场景 | 行为 |
|---|---|
| MCP 传输/执行异常 | `_tool_error_message` 降级「工具执行失败。」、只记类名防 URL 泄漏 |
| MCP server 加载失败 | `load_mcp_tools` 逐 server 跳过，不阻断启动 |
| 畸形参数 | `ToolInvocationError` 原样返回校验信息供 LLM 自纠（消耗 1 轮预算） |
| 轮次耗尽 | call_llm 走无工具路径强制收尾 |

## 测试

| 测试 | 断言 |
|---|---|
| `test_mcp_config.py`（改） | `websearch_enabled` 默认 True；`websearch_enabled=False` 时即使有 key 也不注册 Tavily；`mcp_enabled=False` 时额外 server 不加载、Tavily 单独可加载；既有断言更新到新门控语义 |
| `test_call_llm_node.py`（改） | tools 含 `tavily_search` 命名工具 → 注入 `WEB_SEARCH_HINT`；仅 RAG/记忆工具 → 不注入 |
| `test_graph.py`（改） | 新增图级回环：fake `tavily_search` BaseTool 传入 `create_graph(mcp_tools=[...])`，脚本 LLM 调用 → ToolNode 执行 → 回复（复用 `test_graph_memory_tool_roundtrip` 模式） |

## CLAUDE.md 同步

- 新增「Web 搜索（Tavily MCP）」节：开关语义（`websearch_enabled` vs `mcp_enabled` 安全边界）、`WEB_SEARCH_HINT` 层、工具名清单、env 清单。
- `tool_rounds` 注明为 RAG+记忆+web 共享预算、默认 4、每消息重置。
- RAG 节 env 清单补 `BOT_WEBSEARCH_ENABLED`。

## 风险

- **sensenova flash-lite 对 8 工具 schema**：3 内部 + 5 MCP = 8 个工具绑定，schema 变大；`WEB_SEARCH_HINT` 帮助导航，联调确认模型能正确产出工具调用。
- **crawl/research 耗时**：hint 引导优先 `tavily_search`；真正遇到超时/阻塞再评估工具级超时（YAGNI，不预做）。
- **Tavily 外网可达性**：国内部署可能不可达 → 加载降级跳过，bot 照常运行（沿用上次风险结论）。
- **工具结果撑大上下文**：summarize 前 tool 结果全在 context 里；4 轮上限 + 引导优先 search 缓解。
