# 记忆工具节点化：Memory Tool Node

日期：2026-07-31
状态：已批准（2026-07-31）

## 背景

长期记忆模块 `MemoryStore`（`bot/core/memory.py`）当前是**图外**驱动：

- **读**：进图前 `format_memories(user_id)` 全量注入 `user_memories` 字段，`call_llm_node` 渲染为 SystemMessage 第 2 层。
- **写**：回复后 `_extract_memories()` 用单独一次 LLM 调用（`EXTRACT_PROMPT`）从「用户消息 + Bot 回复」抽取 JSON 事实，`store_memories()` 入库。

LLM 不参与记忆的存/取决策，与 RAG 的「LLM 按需调工具」模式不一致。

目标：把记忆封装为工具（`remember_user_memory` / `recall_user_memory`），接入主图的 `tool_node` 分发器，与 RAG 统一为「LLM 主动管理记忆」。

## 决策

1. **读 + 写工具**（用户已确认）：`remember_user_memory(key, value)` 存、`recall_user_memory(keyword)` 取，LLM 自己决定何时存/取。不包含 `forget`（YAGNI，后续可按需加）。
2. **纯工具检索**（用户已确认）：移除进图前的 `user_memories` 全量注入，不再把记忆塞进 SystemMessage。LLM 需要时主动 `recall`。
3. **tool_node 泛化为分发器**（用户已确认，方案 A）：`rag_tool_node` 重命名为 `tool_node`，按 `tool_call["name"]` 分发到 RAG 检索 / 记忆存取。图结构不变（`call_llm → tool_node → call_llm` 单条回环）。
4. **替代图外抽取**：删除 `handler._extract_memories`、`MessageHandler` 的 `extract_llm` 参数、`EXTRACT_PROMPT`、`MemoryStore.parse_extraction`。记忆写入从「图外回复后单独 LLM 抽取」改为「图内 LLM 主动 remember」。
5. **`MEMORY_TOOL_HINT` 提示层**：call_llm 增加一层 SystemMessage，告知 LLM 记忆工具的存在与用法（注入移除后 LLM 需要提示才会主动 recall）。这是本次行为变化的主要风险缓解手段。
6. **`rag_tool_rounds` 改名为 `tool_rounds`**：回环轮次计数器现在服务所有工具，命名语义对齐。配置键 `BOT_RAG_MAX_AGENT_ROUNDS` **保持不变**（避免破坏现有 env），内部语义变为「工具回环总轮数上限」。
7. **`user_id` 进 state**：记忆按用户维度存储（`MemoryStore` 表以 `(user_id, key)` 为主键），tool_node 需要从 state 注入 `user_id`（类比 `thread_id`）。`session_id` 末段虽是 user_id 但不宜字符串解析，直接加字段。
8. **`MemoryStore` 同步调用异步化**：工具纯函数内用 `asyncio.to_thread` 包裹（对齐 RAG service 的做法），不阻塞事件循环。

## 文件变更

| 文件 | 动作 | 说明 |
|---|---|---|
| `bot/core/tools/user_memory.py` | 新增 | 两个工具：`remember_user_memory` / `recall_user_memory`，schema + 纯函数 + 格式化辅助 |
| `bot/core/tools/__init__.py` | 改 | 导出新增工具 schema 与函数 |
| `bot/core/nodes/tool_node/tool_node.py` | 重命名+重写 | `rag_tool_node` → `tool_node`，按工具名分发到 RAG / 记忆 |
| `bot/core/nodes/tool_node/__init__.py` | 改 | 导出 `tool_node` |
| `bot/core/nodes/__init__.py` | 改 | 导出 `tool_node`（替换 `rag_tool_node`） |
| `bot/core/nodes/llm_node/call_llm.py` | 改 | 绑定 3 工具、删 `user_memories` 注入、加 `MEMORY_TOOL_HINT` 层、计数改名 `tool_rounds` |
| `bot/core/graph.py` | 改 | 注册 `tool_node`，注入 `memory_store` |
| `object/bot/state.py` | 改 | 加 `user_id`；`rag_tool_rounds` → `tool_rounds` |
| `bot/handler.py` | 改 | 删 `_extract_memories`/`format_memories`/`extract_llm` 参数/`user_memories` 字段，state 加 `user_id` |
| `main.py` | 改 | `create_graph` 传 `memory_store`；handler 构造去 `extract_llm` |
| `common/prompts.py` | 改 | 删 `EXTRACT_PROMPT`，加 `MEMORY_TOOL_HINT` |
| `bot/core/memory.py` | 改 | 删 `parse_extraction` 与 `EXTRACT_PROMPT` 引用；保留 store 方法 |
| `CLAUDE.md` | 改 | 记忆段更新为工具驱动；数据流、checkpoint 保证同步 |

## 工具定义

### remember_user_memory

```python
TOOL_SCHEMA_REMEMBER = {
    "type": "function",
    "function": {
        "name": "remember_user_memory",
        "description": "保存当前用户的持久性个人信息（名字、偏好、习惯、背景等）。当用户提到新的持久事实时调用；更新已有记忆时直接以相同 key 覆盖。",
        "parameters": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "记忆的语义标签，如 \"喜欢的食物\""},
                "value": {"type": "string", "description": "记忆内容，中文表述"},
            },
            "required": ["key", "value"],
        },
    },
}

async def remember_user_memory(key: str, value: str, memory_store: MemoryStore, user_id: str) -> str:
    await asyncio.to_thread(memory_store.store_memory, user_id, key, value)
    return f"已记住：{key} = {value}"
```

### recall_user_memory

```python
TOOL_SCHEMA_RECALL = {
    "type": "function",
    "function": {
        "name": "recall_user_memory",
        "description": "检索当前用户的持久记忆（名字、偏好、习惯、背景等）。当需要用户的个人信息、或回想之前提到过的用户事实时使用。keyword 留空返回全部记忆，否则按 key/value 模糊匹配。",
        "parameters": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "检索关键词，按 key/value 模糊匹配；留空返回全部记忆"},
            },
            "required": ["keyword"],
        },
    },
}

async def recall_user_memory(keyword: str, memory_store: MemoryStore, user_id: str) -> str:
    memories = await asyncio.to_thread(memory_store.load_memories, user_id)
    # keyword 为空 → 返回全部；否则 key 或 value 中任一包含 keyword 子串（大小写不敏感）
    # 格式化 "- key：value\n..."；空 → "没有找到相关记忆。"
```

## tool_node 分发器

```python
async def tool_node(state: BotState, rag_service=None, memory_store=None) -> dict:
    messages = state.get("messages") or []
    last = messages[-1] if messages else None
    tool_calls = getattr(last, "tool_calls", None) or []
    if not tool_calls:
        return {}
    thread_id = state.get("thread_id", "")
    user_id = state.get("user_id", "")
    tool_messages = []
    for tc in tool_calls:
        name = tc.get("name", "")
        args = tc.get("args") or {}
        try:
            if name == "search_chat_history":
                content = await search_chat_history(args["query"], rag_service, thread_id)
            elif name == "remember_user_memory":
                content = await remember_user_memory(args["key"], args["value"], memory_store, user_id)
            elif name == "recall_user_memory":
                content = await recall_user_memory(args.get("keyword", ""), memory_store, user_id)
            else:
                content = f"未知工具：{name}"
        except Exception:
            logger.exception("Tool %s failed for session %s", name, state.get("session_id", ""))
            content = "工具执行失败。"
        tool_messages.append(ToolMessage(content=content, tool_call_id=tc.get("id", "")))
    return {"messages": tool_messages}
```

- `rag_service` / `memory_store` 由 `functools.partial` 注入（同现有模式，非闭包，避免多线程串台）。
- 工具调用消息（AIMessage + ToolMessage）持久化到 checkpoint，由 `summarize_node` 统一压缩。

## call_llm_node 改动

```python
persona = state["persona"].format(bot_name=state.get("bot_name", ""))
system_msgs = [SystemMessage(content=persona)]

summary = state.get("conversation_summary", "").strip()
if summary:
    system_msgs.append(SystemMessage(content=f"之前的对话摘要：\n{summary}"))

# 删掉 user_memories 注入；新增 MEMORY_TOOL_HINT 层
system_msgs.append(SystemMessage(content=MEMORY_TOOL_HINT))

messages = system_msgs + state["messages"]

use_rag = rag_service is not None and rag_service.enabled
use_memory = memory_store is not None
schemas = [TOOL_SCHEMA]
if use_memory:
    schemas += [TOOL_SCHEMA_REMEMBER, TOOL_SCHEMA_RECALL]
max_rounds = bot_config.rag_max_agent_rounds if bot_config is not None else 3
rounds = state.get("tool_rounds", 0)

if (use_rag or use_memory) and rounds < max_rounds:
    response = await llm.bind_tools(schemas).ainvoke(messages)
    if response.tool_calls:
        return {"messages": [response], "tool_rounds": rounds + 1, "reply_text": ""}
    return {"messages": [AIMessage(content=response.content)], "reply_text": response.content}
reply = await _invoke_plain(messages, llm, state)
return {"messages": [AIMessage(content=reply)], "reply_text": reply}
```

其中 `MEMORY_TOOL_HINT` 具体文案：

```python
MEMORY_TOOL_HINT = """你可以通过工具读取和保存当前用户的持久记忆（名字、偏好、习惯、背景等）。
- 需要用户的个人信息、或回想之前提到过的用户事实时，调用 recall_user_memory 检索。
- 用户提到新的持久性个人信息时，调用 remember_user_memory 保存。
- 记忆按用户区分，只涉及当前发送消息的用户。"""
```

- 工具绑定条件从 `use_rag` 扩展为 `use_rag or use_memory`（任一服务存在即启用工具路径）。
- 轮次耗尽后仍走无工具路径强制收尾（LLM 无法再产出 tool_calls，天然收尾）。

## 数据流

```
现在：
  handler: format_memories(读) → graph（注入 user_memories）→ 回复 → _extract_memories(写)
改后：
  handler: 无记忆读 → graph
    → call_llm（3 工具 + MEMORY_TOOL_HINT）
      tool_node ← 按 name 分发（RAG 检索 / 记忆存取）→ 回环 call_llm
    → summarize → END
  → 发送回复 → RAG index_turn（保留，图外）
```

记忆写入从「图外回复后单独 LLM 抽取」改为「图内 LLM 主动 remember」。

## 错误处理

- 工具执行异常 → ToolMessage `"工具执行失败。"`，对话不中断（沿用 RAG 语义）。
- `MemoryStore` 同步 sqlite → 工具内 `asyncio.to_thread` 包裹。
- LLM 调用异常 → `_invoke_plain` 的 `"我暂时无法思考，请稍后再试"` 降级文案（沿用）。
- 未知工具名 → ToolMessage `"未知工具：{name}"`（防御，正常不会触发）。

## 测试

- 单元：`remember_user_memory` / `recall_user_memory` 纯函数（`StubMemoryStore`），含 keyword 模糊匹配、空结果。
- 单元：`tool_node` 按 name 分发（RAG / 记忆 / 未知工具）。
- 集成：`call_llm_node` + `tool_node` 回环（`ScriptedLLM` 脚本化 tool_calls → 记忆工具 → 最终回复），验证 `tool_rounds` 上限。
- 图：`create_graph` 编译 + 记忆工具一次回环。
- `tests/fakes.py` 加 `StubMemoryStore`（内存 dict 实现 store/load）。
- 移除依赖 `EXTRACT_PROMPT` / `parse_extraction` / `_extract_memories` 的旧测试用例。

## CLAUDE.md 同步

- **记忆段**：从「进图前注入 + 图外抽取」改为「LLM 通过 remember/recall 工具主动管理」，`extract_llm` 相关描述删除。
- **数据流**：去掉 `extract memories via MemoryStore` 与 user_memories 注入，改为 tool_node 分发。
- **Node type convention**：`tool_node/` 描述更新为「按工具名分发的通用工具节点」。
- **工具定位**：`rag_tool_node` → `tool_node`，`memory_store` 经 partial 注入。
