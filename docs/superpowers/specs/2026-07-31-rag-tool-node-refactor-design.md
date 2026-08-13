# RAG 工具迁移：tools/ + 图级 tool_node

日期：2026-07-31
状态：已批准（2026-07-31）

## 背景

`search_chat_history` RAG 工具当前定义在 `bot/core/rag/tools.py`，由 `call_llm_node` 通过内部 ReAct 循环调用（`bind_tools` + `for _ in range(max_rounds)` 手写循环）。工具调用的中间消息只存在于函数局部，不落 checkpoint。

目标：把工具迁到 `bot/core/tools/`，在 `bot/core/nodes/tool_node/` 定义图级工具节点，并加入主图 —— 采用标准 LangGraph agent 模式。

## 决策

1. **自定义 tool_node**（不用 `langgraph.prebuilt.ToolNode`）。原因：工具需要注入 `thread_id` 与 `rag_service`，prebuilt ToolNode 只能从 `tool_calls` 的 `args` 取参，而 `thread_id` 在 state 里且不宜暴露给 LLM；闭包捕获在 worker 多线程并发时会串台。
2. **工具调用消息持久化到 checkpoint**（用户已确认）。带 `tool_calls` 的 AIMessage 与 ToolMessage 成为对话历史的一部分，由 `summarize_node` 的 `trim_messages` 统一处理。这是对 CLAUDE.md 中"checkpoint 只存 Human/AI 消息"保证的行为变更，需同步更新文档。
3. **`rag_max_agent_rounds` 用 state 计数器** `rag_tool_rounds` 控制，不用 `recursion_limit`。`call_llm` 中 `rounds >= max_rounds` 时改用无工具路径强制收尾。

## 文件变更

| 文件 | 动作 | 说明 |
|---|---|---|
| `bot/core/tools/search_chat_history.py` | 新增 | `search_chat_history(query, rag_service, thread_id) -> str` 纯函数 + `TOOL_SCHEMA` + 格式化辅助 |
| `bot/core/tools/__init__.py` | 改 | 导出 `TOOL_SCHEMA`、`search_chat_history` |
| `bot/core/nodes/tool_node/rag_tool_node.py` | 新增 | 读 state 最后一条消息的 `tool_calls` + `thread_id`，执行工具，返回 ToolMessage 列表 |
| `bot/core/nodes/tool_node/__init__.py` | 改 | 导出 `rag_tool_node` |
| `bot/core/nodes/llm_node/call_llm.py` | 改 | 删除 `_invoke` 循环；一次调用；有 tool_calls → 返回原始 AIMessage + `rag_tool_rounds` 计数 + 空 `reply_text`；无 → 返回最终 AIMessage + `reply_text`；轮次耗尽 → `_invoke_plain` 收尾 |
| `bot/core/graph.py` | 改 | 注册 `tool_node`；`call_llm` 加条件边 → `tool_node` / `summarize`；`tool_node → call_llm` 回边 |
| `object/bot/state.py` | 改 | `BotState` 加 `rag_tool_rounds: int` |
| `bot/handler.py` | 改 | ainvoke 初始 state 加 `"rag_tool_rounds": 0` |
| `bot/core/rag/tools.py` | 删除 | 内容迁入 `bot/core/tools/` |
| `CLAUDE.md` | 改 | RAG 段落、图结构、checkpoint 保证同步更新 |

## 图结构

```
现在：  detect_intent → router → call_llm → summarize → END
改后：  detect_intent → router → call_llm ──┬─ tool_node ──→ call_llm（回边）
                                             └─ summarize → END（无 tool_calls 时）
```

- 条件边：`lambda s: "tool_node" if s["messages"][-1].tool_calls else "summarize"`（`tool_calls` 空列表为 falsy）。
- 每回合 2 个超步（call_llm → tool_node），3 轮上限在默认 `recursion_limit=25` 之内，无需改 ainvoke 配置。

## call_llm_node 新逻辑

```
use_rag = rag_service 存在且启用
rounds = state.get("rag_tool_rounds", 0)
if use_rag and rounds < max_rounds:
    response = await llm.bind_tools([TOOL_SCHEMA]).ainvoke(messages)
    if response.tool_calls:
        return {"messages": [response], "rag_tool_rounds": rounds + 1, "reply_text": ""}
    return {"messages": [AIMessage(content=response.content)], "reply_text": response.content}
else:
    reply = await _invoke_plain(messages, llm, state)
    return {"messages": [AIMessage(content=reply)], "reply_text": reply}
```

## rag_tool_node 逻辑

```
last = state["messages"][-1]
tool_calls = getattr(last, "tool_calls", None) or []
if not tool_calls: return {}
thread_id = state["thread_id"]
对每个 tool_call:
    query = args["query"]
    try: content = await search_chat_history(query, rag_service, thread_id)
    except: content = "检索历史消息失败。"（记日志）
    追加 ToolMessage(content, tool_call_id=tc["id"])
return {"messages": tool_messages}
```

## 错误处理

- 工具执行异常 → ToolMessage 内容为 `"检索历史消息失败。"`，对话不中断（沿用现有语义）。
- LLM 调用异常 → `_invoke_plain` 的 `"我暂时无法思考，请稍后再试"` 降级文案（沿用）。
- 轮次耗尽（`rounds >= max_rounds`）→ `call_llm` 走无工具路径，LLM 无法再产出 `tool_calls`，天然收尾。旧的 `for...else`"循环耗尽"降级分支不再需要。

## 测试

- `uv run python -c` 验证所有模块 import 与 `create_graph` 编译通过。
- 构造 stub `RagService`（`enabled=True`，`search` 返回固定结果），直接调用 `call_llm_node` + `rag_tool_node`，验证：一次 tool_calls 响应 → 路由到 tool_node → 回到 call_llm 后拿到最终回复；以及 `rag_max_agent_rounds` 上限生效。

## CLAUDE.md 同步

- **RAG 段**：ReAct 循环改为图级循环（call_llm ↔ tool_node），`make_search_tool` 工厂被 `rag_tool_node` 替代。
- **Node type convention**：`tool_node/` 从"future"变为实际节点，标注 `rag_tool_node`。
- **数据流**：加入 tool_node 路由。
- **checkpoint 保证**：由"只存 HumanMessage + AIMessage"改为"Human/AI/Tool 消息都会持久化，SystemMessage 除外"。
