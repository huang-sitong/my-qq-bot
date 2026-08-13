# MCP 外部工具接入 + ToolNode 迁移（Tavily web search 为第一个 MCP server）

日期：2026-08-05
状态：已批准（2026-08-05）

## 背景

目标：引入 Tavily 实现 web search。用户规划接入**大量外部工具**并选定 **MCP 生态**路线，因此 Tavily 走 MCP，而不是 `langchain-tavily` 直连（后者仅适合单一 Tavily 场景）。

现状核实：

- **langgraph 1.2.2 无内置 MCP**（`prebuilt` 只有 `ToolNode`/`create_react_agent`/`tools_condition`）。MCP 必须引入 `mcp`（官方 Python SDK）+ `langchain-mcp-adapters`（0.3.1）。
- **Tavily 官方 MCP server** 是 npm 的 `tavily-mcp`（`@tavily/mcp-server` 在 npm 不存在），提供 `tavily-search` / `tavily-extract` / `tavily-map` / `tavily-crawl`。**官方托管远程端点** `https://mcp.tavily.com/mcp/?tavilyApiKey=<key>` 支持 streamable_http 直连，无需 Node、无子进程。⚠️ PyPI 上另有同名 `tavily-mcp` 社区包（HTTP 端口形态、维护质量未知），禁止使用。
- **`MultiServerMCPClient` 不是 context manager**；`get_tools()` 返回 LangChain `BaseTool`，**每个工具调用时才自建 session**（stdio=每次拉子进程，streamable_http=每次新建 HTTP 会话）。`handle_tool_errors=True` 默认把 MCP 执行错误转成 `status="error"` 的 `ToolMessage`；`tool_name_prefix` 可加 server 名前缀防多 server 工具名冲突。

## 决策（用户已确认）

1. **迁移到 `langgraph.prebuilt.ToolNode`**，弃用自定义 `tool_node` 手工分发。
   - 历史：`2026-07-31-rag-tool-node-refactor-design.md` 曾弃用 ToolNode，理由是"prebuilt 只能从 tool_calls.args 取参、thread_id 在 state 里且不宜暴露给 LLM；闭包捕获并发串台"。
   - 现状：langgraph 1.2.2 提供 `InjectedState`（`InjectedToolArg` 子类，已核实源码）。thread_id/user_id 用 `InjectedState("thread_id")`/`InjectedState("user_id")` **按次从 graph state 注入**——LLM-facing schema 自动排除（`tool_node.py` L1815 明示）、ToolNode 执行时注入，两个障碍均消除。rag_service/memory_store 仍经 `functools.partial`/闭包绑定（本就是跨线程共享的只读服务，非每调用变量，无串台风险）。
2. **内部工具归一为 `BaseTool`**，schema 由函数签名 + `description` 自动推断，**删除全部手写 `TOOL_SCHEMA` 裸 dict**（单一来源，消除 schema 与签名漂移）。
3. **降级语义保留**：内部工具包装层捕获异常 → 返回占位文案 `"工具执行失败。"`，真实错误记日志，原始异常不进 checkpoint。这是刻意的产品决策——用户看到优雅文案，LLM 不会接触原始异常。
4. **Tavily 走官方远程端点**（streamable_http），连接零成本、无生命周期管理。
5. **MCP 工具执行错误**由 adapters `handle_tool_errors=True` 转成 `status="error"` ToolMessage，LLM 可自我修正（与内部工具占位文案两种降级路径，均不中断对话）。
6. **工具循环守卫**从 `if (use_rag or use_memory)` 改为 **`if tools and rounds < max_rounds`**——顺带修掉"关 RAG 就丢全部工具"的既有隐患。`tool_rounds` 预算逻辑与 `MEMORY_TOOL_HINT` 注入保持不变。

## 依赖

```
uv add mcp langchain-mcp-adapters
```

`langchain-mcp-adapters` 自动拉入官方 `mcp` SDK。langgraph 1.2.2 无需升级。

## 文件变更

| 文件 | 动作 | 说明 |
|---|---|---|
| `bot/core/tools/factory.py` | 新增 | `build_tools(rag_service, memory_store, mcp_tools)` → `list[BaseTool]`；内部工具包装（InjectedState + 异常降级） |
| `bot/core/tools/__init__.py` | 改 | 导出 `build_tools`；保留纯函数导出，删除/停用 `TOOL_SCHEMA*` 裸 dict |
| `bot/core/tools/search_chat_history.py` | 改 | 纯函数保留；删除 `TOOL_SCHEMA`/`TOOL_NAME`/`TOOL_DESCRIPTION` 中仅供 schema 用的部分（`TOOL_DESCRIPTION` 移入 factory 作为 description） |
| `bot/core/tools/user_memory.py` | 改 | 纯函数保留；删除 `TOOL_SCHEMA_REMEMBER`/`TOOL_SCHEMA_RECALL`（description 移入 factory） |
| `bot/core/mcp/__init__.py` | 新增 | 导出 `load_mcp_tools` |
| `bot/core/mcp/client.py` | 新增 | `load_mcp_tools(servers, tool_name_prefix=False)` → 逐 server 加载，单个失败降级跳过 |
| `bot/core/nodes/llm_node/call_llm.py` | 改 | 签名改 `(state, llm, tools=None, use_memory=False, bot_config=None)`；`llm.bind_tools(tools)`；守卫改 `if tools and rounds < max_rounds` |
| `bot/core/nodes/tool_node/tool_node.py` | 删除 | 由 `ToolNode` 取代（`tool_node/` 目录移除） |
| `bot/core/nodes/__init__.py` | 改 | 移除 `tool_node` 导出，改引 `ToolNode` |
| `bot/core/graph.py` | 改 | `build_tools` 构建工具列表；注册 `tools` 节点 `ToolNode(tools)`；`call_llm` 条件边 → `tools`/`summarize`；`tools → call_llm` 回边 |
| `bot/core/__init__.py` / `bot/__init__.py` | 改 | 按需调整导出 |
| `common/config.py` | 改 | `BotConfig` 加 4 字段 + `mcp_server_connections()` |
| `main.py` | 改 | `mcp_enabled` 时 `await load_mcp_tools(...)` → 传入 `create_graph` |
| `.env-template` | 改 | `TAVILY_API_KEY`（用户已加）+ `BOT_MCP_ENABLED` / `BOT_MCP_SERVERS` / `BOT_MCP_TOOL_NAME_PREFIX` |
| `CLAUDE.md` | 改 | 工具循环段、`bot/core/mcp/`、Node type convention、env 清单 |
| `tests/test_tool_node.py` | 重写 | 驱动 `ToolNode(build_tools(...))` |
| `tests/test_memory_store_tool_integration.py` | 改 | `tool_node(...)` → `ToolNode(build_tools(memory_store=store))` |
| `tests/test_call_llm_node.py` | 改 | 改传 `tools=` + `use_memory=` |
| `tests/test_tools_factory.py` | 新增 | schema 不含 thread_id/user_id；服务开关驱动工具出现/消失 |
| `tests/test_mcp_config.py` | 新增 | Tavily URL 构造、JSON 解析、开关门控 |
| `tests/test_mcp_local_server.py` | 新增 | 本地 `FastMCP` stdio 子进程端到端（不打外网） |

## 图结构

```
改后：  detect_intent → describe_image → call_llm ──┬─ tools（ToolNode）──→ call_llm（回边）
                                                   └─ summarize → index_turn → END（无 tool_calls）
```

- 条件边不变：`lambda s: "tools" if getattr(s["messages"][-1], "tool_calls", None) else "summarize"`。
- `ToolNode` 空列表可安全构造（已核实源码 `__init__` 无空校验）；`tools` 为空时 call_llm 走 `_invoke_plain`，永不产出 tool_calls，`tools` 节点不会被路由到。

## call_llm_node 新逻辑

```
tools = 注入的 list[BaseTool]
use_memory = 注入的 bool（MEMORY_TOOL_HINT 用）
rounds = state.get("tool_rounds", 0)
if tools and rounds < max_rounds:
    response = await llm.bind_tools(tools).ainvoke(messages)
    if response.tool_calls:
        return {"messages": [response], "tool_rounds": rounds + 1, "reply_text": ""}
    return {"messages": [AIMessage(content=response.content)], "reply_text": response.content}
else:
    reply = await _invoke_plain(messages, llm, state)   # 无工具 / 轮次耗尽
    return {"messages": [AIMessage(content=reply)], "reply_text": reply}
```

- 混合 BaseTool 与裸 dict 不再存在——统一为 `bind_tools(tools)`，`ScriptedLLM.bind_tools` 忽略参数，图级测试无需改断言。
- `tool_rounds` 递增仍在 call_llm（ToolNode 不碰）；`messages` 通道 `add_messages` reducer 兼容 ToolNode 的 `{"messages": [...]}` 返回。

## tools 统一层（`bot/core/tools/factory.py`）

```python
def build_tools(rag_service=None, memory_store=None, mcp_tools=None) -> list[BaseTool]:
    tools = []
    if rag_service is not None and rag_service.enabled:
        tools.append(_make_search_tool(rag_service))
    if memory_store is not None:
        tools += _make_memory_tools(memory_store)
    tools += list(mcp_tools or [])
    return tools
```

内部工具包装模式（闭包绑服务 + `InjectedState` 注入 + 异常降级）：

```python
def _make_search_tool(rag_service) -> BaseTool:
    async def _run(
        query: str,
        user_name: str = "",
        hours: int = 0,
        content_keyword: str = "",
        start_time: str = "",
        end_time: str = "",
        thread_id: Annotated[str, InjectedState("thread_id")] = "",
    ) -> str:
        try:
            return await search_chat_history(query, rag_service, thread_id,
                                             user_name, hours, content_keyword,
                                             start_time, end_time)
        except Exception:
            logger.exception("search_chat_history failed")
            return "工具执行失败。"
    return StructuredTool.from_function(func=_run, name="search_chat_history",
                                        description=SEARCH_TOOL_DESCRIPTION)
```

`remember_user_memory` / `recall_user_memory` 同理（`user_id` 用 `InjectedState("user_id")`）。

## MCP 集成模块（`bot/core/mcp/client.py`）

```python
async def load_mcp_tools(servers: dict, *, tool_name_prefix: bool = False) -> list[BaseTool]:
    client = MultiServerMCPClient(servers, tool_name_prefix=tool_name_prefix)
    tools = []
    for name in client.connections:          # 逐 server 加载，单个失败降级跳过
        try:
            tools += await client.get_tools(server_name=name)
        except Exception:
            logger.exception("MCP server %s 加载失败，跳过", name)
    return tools
```

生命周期零成本：工具每次调用自建 session（streamable_http=新 HTTP 会话），无需持有连接、无需 shutdown 钩子。server 加载失败不阻断 bot 启动。

## 配置（`BotConfig` 新增）

```python
mcp_enabled: bool          # BOT_MCP_ENABLED，默认 0（"0"/"false"/"False"/"" 视为关）
mcp_servers: dict          # BOT_MCP_SERVERS，JSON 字符串 → 额外 server（可多 transport）
mcp_tool_name_prefix: bool # BOT_MCP_TOOL_NAME_PREFIX，默认 0；多 server 防工具名冲突
tavily_api_key: str        # TAVILY_API_KEY

def mcp_server_connections(self) -> dict:
    servers = dict(self.mcp_servers)
    if self.tavily_api_key.strip():
        servers.setdefault("tavily", {
            "transport": "streamable_http",
            "url": f"https://mcp.tavily.com/mcp/?tavilyApiKey={self.tavily_api_key.strip()}",
        })
    return servers
```

`BOT_MCP_SERVERS` 示例：`{"weather": {"transport": "streamable_http", "url": "http://localhost:8000/mcp"}}`。API key 仅在内存 config 中，不进 state/messages/checkpoint。

`main.py`：`config.mcp_enabled` 时 `await load_mcp_tools(config.mcp_server_connections())` → 传入 `create_graph`。Tavily 端点不可达 → 加载跳过，bot 照常启动。

## 错误处理

| 场景 | 行为 |
|---|---|
| 内部工具执行异常 | 包装层捕获 → `"工具执行失败。"`（记日志） |
| MCP 工具执行错误 | adapters `handle_tool_errors=True` → `status="error"` ToolMessage，LLM 可自我修正 |
| MCP server 加载失败 | `load_mcp_tools` 跳过该 server，记日志，不阻断启动 |
| 未知工具名 | ToolNode `handle_tool_errors=True` → error ToolMessage（正确绑定时不应发生） |
| LLM 调用异常 | `_invoke_plain` 的 `"我暂时无法思考，请稍后再试"`（沿用） |
| 轮次耗尽 | call_llm 走无工具路径强制收尾（沿用） |

## 测试

| 测试 | 断言 |
|---|---|
| `test_tools_factory.py` | `build_tools` 的 schema 不含 `thread_id`/`user_id`；rag/memory 开关驱动工具出现/消失；内部工具异常 → `"工具执行失败。"` |
| `test_tool_node.py`（重写） | `ToolNode(build_tools(...))` 执行 RAG 三路（query/user/time）、记忆 recall/remember、降级 |
| `test_memory_store_tool_integration.py`（改） | 真实 `MemoryStore` 经 `ToolNode` 走 to_thread 路径 |
| `test_call_llm_node.py`（改） | `tools=` 空/非空 + `use_memory=` 驱动的绑定与提示注入；轮次预算 |
| `test_graph.py` | 现有断言基本不变（`ScriptedLLM` 忽略 bind_tools），验证通过即回归 |
| `test_mcp_config.py` | `mcp_server_connections()` 构造 Tavily URL、JSON 解析、门控 |
| `test_mcp_local_server.py` | 本地 `FastMCP` stdio 子进程：`load_mcp_tools` → `ToolNode` 执行 → ToolMessage 内容正确 |

`test_mcp_local_server.py` 用 `mcp` SDK 的 `FastMCP` 起 stdio 子进程（`uv` venv 内 `python -c` 起 server），验证 MCP 全链路，**不打外网**。

## CLAUDE.md 同步

- **工具循环段**：自定义 tool_node → `ToolNode`；`TOOL_SCHEMA` 裸 dict → 签名推断；守卫改 `if tools and rounds < max_rounds`。
- **新增 `bot/core/mcp/`**：远程 streamable_http、逐 server 加载降级、零生命周期。
- **Node type convention**：`tool_node/` 目录移除，`tools` 节点为 prebuilt `ToolNode`。
- **数据流**：`call_llm → tools → call_llm` 回环。
- **env 清单**：`BOT_MCP_ENABLED` / `BOT_MCP_SERVERS` / `BOT_MCP_TOOL_NAME_PREFIX` / `TAVILY_API_KEY`。

## 风险

- **外网可达性**：`mcp.tavily.com` 对国内部署是否可达（LLM 是国内 sensenova、嵌入/视觉是本地 Ollama）。已降级兜底：不可达 → 无 web 工具，bot 照常运行。
- **sensenova tool-calling 对复杂 schema**：`tavily-search` 参数较多（query/max_results/search_depth/include_domains 等），需联调确认模型能正确产出工具调用。
- **`tool_rounds` 共享预算**：默认 `rag_max_agent_rounds=3` 现由 RAG+记忆+MCP 共享；web 多轮场景（先搜再 extract）可能要调大，实现后按需评估。
