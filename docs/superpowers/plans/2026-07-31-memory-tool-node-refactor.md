# 记忆工具节点化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把长期记忆从「图外全量注入 + 图外抽取」改为「LLM 通过 remember/recall 工具主动管理」，并泛化 tool_node 为按工具名分发的通用工具节点。

**Architecture:** 新增 `bot/core/tools/user_memory.py`（两个工具 schema + 纯函数），把 `bot/core/nodes/tool_node/rag_tool_node.py` 重写为按 `tool_call["name"]` 分发的 `tool_node`（RAG 检索 / 记忆存取），`call_llm` 同时绑定三个工具；移除 `user_memories` SystemMessage 注入与图外 `_extract_memories` 抽取。图结构不变：`detect_intent → router → call_llm → tool_node → call_llm（回环）| summarize → END`。

**Tech Stack:** Python ≥3.12、uv、LangGraph（AsyncSqliteSaver）、langchain_core messages、sqlite3（MemoryStore）。

## Global Constraints

- 测试命令：`uv run pytest tests/ -v`（pytest 9.x，testpaths=tests，pythonpath=.）。
- 工具依赖注入用 `functools.partial`（graph.py 组装），工具纯函数签名 `(…, service, user_id|thread_id)`，依赖由 tool_node 在调用时注入——LLM 无需知道内部标识。
- `MemoryStore` 是同步 sqlite3 → 工具内用 `asyncio.to_thread` 包裹，不阻塞事件循环。
- 工具回环轮次上限由 state 计数器 `tool_rounds` 控制；配置键 `BOT_RAG_MAX_AGENT_ROUNDS` **保持不变**（语义变为「工具回环总轮数上限」）。
- 工具执行异常 → ToolMessage 占位文案，对话不中断（沿用 RAG 语义）。
- 工具调用消息（带 tool_calls 的 AIMessage + ToolMessage）持久化到 checkpoint。
- 代码注释与工具 description 用中文，风格与 `bot/core/tools/search_chat_history.py` 一致。
- 全部开发直接提交到 `master` 分支。

---

### Task 1: 记忆工具纯函数 + StubMemoryStore

**Files:**
- Create: `bot/core/tools/user_memory.py`
- Modify: `bot/core/tools/__init__.py`
- Modify: `tests/fakes.py`
- Create: `tests/test_user_memory.py`

**Interfaces:**
- Produces: `TOOL_SCHEMA_REMEMBER`、`TOOL_SCHEMA_RECALL`（dict，OpenAI function-calling schema）；`async remember_user_memory(key, value, memory_store, user_id) -> str`；`async recall_user_memory(keyword, memory_store, user_id) -> str`；`_format_memories(memories) -> str`。
- Produces: `tests.fakes.StubMemoryStore` — `store_memory(user_id, key, value)`、`load_memories(user_id) -> list[{"key","value"}]`。Task 2/3/4 复用。

- [ ] **Step 1: 写失败测试 + StubMemoryStore**

`tests/fakes.py` 追加（保留现有类不动）：

```python
class StubMemoryStore:
    """内存版 MemoryStore，供记忆工具测试。"""

    def __init__(self):
        self._data: dict[tuple[str, str], str] = {}

    def store_memory(self, user_id: str, key: str, value: str) -> None:
        self._data[(user_id, key)] = value

    def load_memories(self, user_id: str) -> list[dict]:
        return [
            {"key": k, "value": v}
            for (uid, k), v in self._data.items()
            if uid == user_id
        ]
```

新建 `tests/test_user_memory.py`：

```python
import asyncio

from bot.core.tools import (
    TOOL_SCHEMA_RECALL,
    TOOL_SCHEMA_REMEMBER,
    recall_user_memory,
    remember_user_memory,
)
from bot.core.tools.user_memory import _format_memories
from tests.fakes import StubMemoryStore


def test_schemas_expose_expected_params():
    fn = TOOL_SCHEMA_REMEMBER["function"]
    assert fn["name"] == "remember_user_memory"
    assert "key" in fn["parameters"]["properties"]
    assert "value" in fn["parameters"]["properties"]

    fn = TOOL_SCHEMA_RECALL["function"]
    assert fn["name"] == "recall_user_memory"
    assert "keyword" in fn["parameters"]["properties"]


def test_remember_stores_by_user():
    store = StubMemoryStore()
    text = asyncio.run(remember_user_memory("名字", "张三", store, "u1"))
    assert text == "已记住：名字 = 张三"
    assert store.load_memories("u1") == [{"key": "名字", "value": "张三"}]
    assert store.load_memories("u2") == []


def test_recall_returns_all_when_keyword_empty():
    store = StubMemoryStore()
    store.store_memory("u1", "名字", "张三")
    store.store_memory("u1", "喜欢的食物", "火锅")
    text = asyncio.run(recall_user_memory("", store, "u1"))
    assert "名字：张三" in text
    assert "喜欢的食物：火锅" in text


def test_recall_filters_by_keyword_substring():
    store = StubMemoryStore()
    store.store_memory("u1", "喜欢的食物", "火锅")
    store.store_memory("u1", "城市", "上海")
    text = asyncio.run(recall_user_memory("食物", store, "u1"))
    assert "火锅" in text
    assert "上海" not in text


def test_recall_empty_result():
    store = StubMemoryStore()
    text = asyncio.run(recall_user_memory("", store, "u1"))
    assert text == "没有找到相关记忆。"


def test_format_memories_renders_lines():
    assert _format_memories([{"key": "名字", "value": "张三"}]) == "- 名字：张三"
    assert _format_memories([]) == "没有找到相关记忆。"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `uv run pytest tests/test_user_memory.py -v`
Expected: FAIL，`ImportError: cannot import name 'TOOL_SCHEMA_REMEMBER' from 'bot.core.tools'`

- [ ] **Step 3: 实现 `bot/core/tools/user_memory.py`**

```python
"""用户记忆工具：remember_user_memory / recall_user_memory。

纯函数：保存/检索当前用户的持久记忆并格式化为文本。
memory_store 与 user_id 由 tool_node 在调用时注入，LLM 无需知道内部标识。
"""

import asyncio

TOOL_NAME_REMEMBER = "remember_user_memory"
TOOL_NAME_RECALL = "recall_user_memory"

TOOL_SCHEMA_REMEMBER = {
    "type": "function",
    "function": {
        "name": TOOL_NAME_REMEMBER,
        "description": "保存当前用户的持久性个人信息（名字、偏好、习惯、背景等）。"
        "当用户提到新的持久事实时调用；更新已有记忆时直接以相同 key 覆盖。",
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

TOOL_SCHEMA_RECALL = {
    "type": "function",
    "function": {
        "name": TOOL_NAME_RECALL,
        "description": "检索当前用户的持久记忆（名字、偏好、习惯、背景等）。"
        "当需要用户的个人信息、或回想之前提到过的用户事实时使用。"
        "keyword 留空返回全部记忆，否则按 key/value 模糊匹配。",
        "parameters": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "检索关键词，按 key/value 模糊匹配；留空返回全部记忆",
                },
            },
            "required": ["keyword"],
        },
    },
}


def _format_memories(memories: list[dict]) -> str:
    if not memories:
        return "没有找到相关记忆。"
    return "\n".join(f"- {m['key']}：{m['value']}" for m in memories)


async def remember_user_memory(key: str, value: str, memory_store, user_id: str) -> str:
    """保存一条用户记忆并返回确认文案。"""
    await asyncio.to_thread(memory_store.store_memory, user_id, key, value)
    return f"已记住：{key} = {value}"


async def recall_user_memory(keyword: str, memory_store, user_id: str) -> str:
    """检索用户记忆；keyword 为空返回全部，否则按 key/value 子串匹配。"""
    memories = await asyncio.to_thread(memory_store.load_memories, user_id)
    keyword = (keyword or "").strip().lower()
    if keyword:
        memories = [
            m for m in memories
            if keyword in m["key"].lower() or keyword in m["value"].lower()
        ]
    return _format_memories(memories)
```

- [ ] **Step 4: `bot/core/tools/__init__.py` 导出**

整体替换为：

```python
from .search_chat_history import TOOL_NAME, TOOL_SCHEMA, search_chat_history
from .user_memory import (
    TOOL_NAME_RECALL,
    TOOL_NAME_REMEMBER,
    TOOL_SCHEMA_RECALL,
    TOOL_SCHEMA_REMEMBER,
    recall_user_memory,
    remember_user_memory,
)

__all__ = [
    "TOOL_NAME", "TOOL_SCHEMA", "search_chat_history",
    "TOOL_NAME_REMEMBER", "TOOL_NAME_RECALL",
    "TOOL_SCHEMA_REMEMBER", "TOOL_SCHEMA_RECALL",
    "recall_user_memory", "remember_user_memory",
]
```

- [ ] **Step 5: 运行测试验证通过**

Run: `uv run pytest tests/test_user_memory.py -v`
Expected: PASS（6 passed）

- [ ] **Step 6: 回归全量**

Run: `uv run pytest tests/ -v`
Expected: PASS（12 旧测试 + 6 新测试 = 18）

- [ ] **Step 7: Commit**

```bash
git add bot/core/tools/user_memory.py bot/core/tools/__init__.py tests/fakes.py tests/test_user_memory.py
git commit -m "feat: 新增用户记忆工具 remember/recall_user_memory 与 StubMemoryStore"
```

---

### Task 2: tool_node 分发器（rag_tool_node → tool_node）

**Files:**
- Modify: `object/bot/state.py`（加 `user_id` 字段）
- Create: `bot/core/nodes/tool_node/tool_node.py`；Delete: `bot/core/nodes/tool_node/rag_tool_node.py`
- Modify: `bot/core/nodes/tool_node/__init__.py`
- Modify: `bot/core/nodes/__init__.py`
- Modify: `bot/core/graph.py`
- Modify: `tests/fakes.py`（`make_state` 加 `user_id`）
- Rename+rewrite: `tests/test_rag_tool_node.py` → `tests/test_tool_node.py`

**Interfaces:**
- Consumes: Task 1 的 `search_chat_history`、`remember_user_memory`、`recall_user_memory`、`StubMemoryStore`。
- Produces: `async tool_node(state, rag_service=None, memory_store=None) -> dict`。
- Produces: `create_graph(llm, config, db_dir="db", rag_service=None, memory_store=None)`（本任务 memory_store 只传给 tool_node；传给 call_llm 是 Task 4）。

- [ ] **Step 1: state 加 user_id 字段**

`object/bot/state.py` 在 `thread_id` 行后加一行：

```python
    thread_id: str        # checkpoint isolation key = platform:guild:channel
    user_id: str          # 当前消息发送者的用户 ID（记忆工具按用户维度存取）
```

`tests/fakes.py` 的 `make_state` 加 `"user_id": "u1",`（`thread_id` 之后）。

- [ ] **Step 2: 写失败测试（新建 test_tool_node.py）**

`git mv tests/test_rag_tool_node.py tests/test_tool_node.py`，整体替换为：

```python
import asyncio

from langchain_core.messages import AIMessage

from bot.core.nodes.tool_node import tool_node
from tests.fakes import StubMemoryStore, StubRagService, make_state

RAG_CALL = AIMessage(content="", tool_calls=[
    {"name": "search_chat_history", "args": {"query": "之前聊了什么"},
     "id": "call_1", "type": "tool_call"},
])
RECALL_CALL = AIMessage(content="", tool_calls=[
    {"name": "recall_user_memory", "args": {"keyword": "食物"},
     "id": "call_2", "type": "tool_call"},
])
REMEMBER_CALL = AIMessage(content="", tool_calls=[
    {"name": "remember_user_memory", "args": {"key": "喜欢的食物", "value": "火锅"},
     "id": "call_3", "type": "tool_call"},
])
UNKNOWN_CALL = AIMessage(content="", tool_calls=[
    {"name": "no_such_tool", "args": {}, "id": "call_4", "type": "tool_call"},
])

SAMPLE = [
    {"thread_id": "test:thread", "user_id": "u1", "user_name": "张三",
     "content": "之前聊了 RAG", "role": "user", "timestamp": 1753910400,
     "score": 0.8},
]


def test_dispatches_search_chat_history_to_rag():
    rag = StubRagService(search_results=SAMPLE)
    state = make_state(messages=[RAG_CALL])
    result = asyncio.run(tool_node(state, rag_service=rag, memory_store=StubMemoryStore()))
    assert "之前聊了 RAG" in result["messages"][0].content
    assert rag.last_query == "之前聊了什么"
    assert rag.last_thread_id == "test:thread"


def test_dispatches_recall_to_memory():
    store = StubMemoryStore()
    store.store_memory("u1", "喜欢的食物", "火锅")
    state = make_state(messages=[RECALL_CALL], user_id="u1")
    result = asyncio.run(tool_node(state, memory_store=store))
    assert "火锅" in result["messages"][0].content


def test_dispatches_remember_to_memory():
    store = StubMemoryStore()
    state = make_state(messages=[REMEMBER_CALL], user_id="u1")
    result = asyncio.run(tool_node(state, memory_store=store))
    assert "已记住" in result["messages"][0].content
    assert store.load_memories("u1") == [{"key": "喜欢的食物", "value": "火锅"}]


def test_unknown_tool_returns_placeholder():
    state = make_state(messages=[UNKNOWN_CALL])
    result = asyncio.run(tool_node(state))
    assert result["messages"][0].content == "未知工具：no_such_tool"


def test_noop_without_tool_calls():
    state = make_state(messages=[AIMessage(content="普通回复")])
    result = asyncio.run(tool_node(state))
    assert result == {}


def test_degrades_on_tool_error():
    rag = StubRagService(raise_on_search=True)
    state = make_state(messages=[RAG_CALL])
    result = asyncio.run(tool_node(state, rag_service=rag))
    assert result["messages"][0].content == "工具执行失败。"
```

Run: `uv run pytest tests/test_tool_node.py -v`
Expected: FAIL，`ImportError: cannot import name 'tool_node' from 'bot.core.nodes.tool_node'`

- [ ] **Step 3: 实现 tool_node 分发器**

`git mv bot/core/nodes/tool_node/rag_tool_node.py bot/core/nodes/tool_node/tool_node.py`，整体替换为：

```python
"""tool_node — 执行 call_llm 请求的工具调用。

读取 state 最后一条消息的 tool_calls，按工具名分发执行：
- search_chat_history   → rag_service 检索（thread_id 注入）
- remember_user_memory  → memory_store 保存（user_id 注入）
- recall_user_memory    → memory_store 检索（user_id 注入）

结果以 ToolMessage 写回 state。失败仅降级为占位文案，不中断对话。
"""

import logging

from langchain_core.messages import ToolMessage

from bot.core.tools import (
    recall_user_memory,
    remember_user_memory,
    search_chat_history,
)
from object.bot.state import BotState

logger = logging.getLogger(__name__)


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
                content = await search_chat_history(args.get("query", ""), rag_service, thread_id)
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

- [ ] **Step 4: 更新导出**

`bot/core/nodes/tool_node/__init__.py` 整体替换：

```python
from .tool_node import tool_node

__all__ = ["tool_node"]
```

`bot/core/nodes/__init__.py` 整体替换：

```python
from .action_node import detect_intent, summarize_node
from .llm_node import call_llm_node, router_node
from .tool_node import tool_node

__all__ = ["call_llm_node", "detect_intent", "router_node", "summarize_node", "tool_node"]
```

- [ ] **Step 5: graph.py 注入 memory_store（只给 tool_node）**

`bot/core/graph.py` 改动两处：

签名（第 18-23 行）加 `memory_store=None`：

```python
async def create_graph(
    llm: ChatOpenAI,
    config: BotConfig,
    db_dir: str = "db",
    rag_service=None,
    memory_store=None,
) -> tuple[CompiledStateGraph, AsyncSqliteSaver]:
```

import 行（第 11 行）`rag_tool_node` → `tool_node`；tool_node 注册（第 36 行）：

```python
    builder.add_node("tool_node", partial(tool_node, rag_service=rag_service, memory_store=memory_store))
```

`call_llm` 的 partial 暂不改（Task 3 才接受 memory_store 参数）。

- [ ] **Step 6: 运行测试验证通过**

Run: `uv run pytest tests/ -v`
Expected: PASS（21 passed：`test_rag_tool_node.py` 的 3 个用例被 `test_tool_node.py` 的 6 个替代，18 − 3 + 6 = 21；`test_graph.py` 仍绿——graph 的 call_llm 尚未接 memory_store，不影响现有行为）

- [ ] **Step 7: Commit**

```bash
git add -A object/bot/state.py bot/core/nodes bot/core/graph.py tests
git commit -m "refactor: rag_tool_node 泛化为按工具名分发的 tool_node"
```

---

### Task 3: call_llm 多工具绑定 + tool_rounds 改名 + MEMORY_TOOL_HINT

**Files:**
- Modify: `object/bot/state.py`（`rag_tool_rounds` → `tool_rounds`）
- Modify: `bot/core/nodes/llm_node/call_llm.py`
- Modify: `common/prompts.py`（加 `MEMORY_TOOL_HINT`）
- Modify: `common/__init__.py`（导出 `MEMORY_TOOL_HINT`）
- Modify: `bot/handler.py`（ainvoke state 键 `rag_tool_rounds` → `tool_rounds`）
- Modify: `tests/fakes.py`（`make_state` 键改名 + `ScriptedLLM.last_messages` 记录）
- Rewrite: `tests/test_call_llm_node.py`

**Interfaces:**
- Consumes: Task 1 的 `TOOL_SCHEMA`/`TOOL_SCHEMA_REMEMBER`/`TOOL_SCHEMA_RECALL`、`MEMORY_TOOL_HINT`（本任务新增）。
- Produces: `call_llm_node(state, llm, rag_service=None, memory_store=None, bot_config=None)`；`MEMORY_TOOL_HINT`（str）。
- 注意：本任务 handler.py 只改 ainvoke 的键名；`user_memories`/`_extract_memories` 的删除在 Task 4。

- [ ] **Step 1: state 计数器改名 + ScriptedLLM 记录消息**

`object/bot/state.py` 第 29 行改为：

```python
    tool_rounds: int       # 工具调用轮次计数（call_llm 递增，工具回环上限）
```

`tests/fakes.py` 的 `make_state` 里 `"rag_tool_rounds": 0,` → `"tool_rounds": 0,`。

`tests/fakes.py` 的 `ScriptedLLM` 增加 `last_messages` 记录（供断言 SystemMessage 层）：

```python
class ScriptedLLM:
    """按序返回脚本响应的假 LLM。"""

    def __init__(self, responses: list[AIMessage]):
        self._responses = list(responses)
        self._index = 0
        self.last_messages = None

    def bind_tools(self, tools, **kwargs):
        return self

    async def ainvoke(self, messages, **kwargs):
        if self._index >= len(self._responses):
            raise AssertionError("ScriptedLLM exhausted: no more scripted responses")
        self.last_messages = list(messages)
        msg = self._responses[self._index]
        self._index += 1
        return msg
```

- [ ] **Step 2: 加 MEMORY_TOOL_HINT**

`common/prompts.py` 末尾追加：

```python
MEMORY_TOOL_HINT = """你可以通过工具读取和保存当前用户的持久记忆（名字、偏好、习惯、背景等）。
- 需要用户的个人信息、或回想之前提到过的用户事实时，调用 recall_user_memory 检索。
- 用户提到新的持久性个人信息时，调用 remember_user_memory 保存。
- 记忆按用户区分，只涉及当前发送消息的用户。"""
```

`common/__init__.py` import 与 `__all__` 加入 `MEMORY_TOOL_HINT`。

- [ ] **Step 3: 写失败测试（重写 test_call_llm_node.py）**

整体替换 `tests/test_call_llm_node.py`：

```python
import asyncio

from langchain_core.messages import AIMessage, HumanMessage

from bot.core.nodes import call_llm_node
from common import BotConfig
from tests.fakes import ScriptedLLM, StubMemoryStore, StubRagService, make_state

TOOL_CALLS = [
    {"name": "search_chat_history", "args": {"query": "x"}, "id": "call_1", "type": "tool_call"},
]
MEMORY_CALLS = [
    {"name": "recall_user_memory", "args": {"keyword": "食物"}, "id": "call_2", "type": "tool_call"},
]
BASE = make_state(messages=[HumanMessage(content="你好")])
CONFIG_ON = BotConfig(rag_enabled=True)


def test_returns_tool_calls_when_services_enabled():
    llm = ScriptedLLM([AIMessage(content="", tool_calls=TOOL_CALLS)])
    state = BASE | {"tool_rounds": 0}
    result = asyncio.run(call_llm_node(
        state, llm=llm, rag_service=StubRagService(), memory_store=StubMemoryStore(),
        bot_config=CONFIG_ON,
    ))
    assert result["reply_text"] == ""
    assert result["tool_rounds"] == 1
    assert result["messages"][0].tool_calls


def test_returns_memory_tool_calls_when_only_memory():
    # rag_service=None → use_rag False；memory_store 存在 → 仍走工具路径
    llm = ScriptedLLM([AIMessage(content="", tool_calls=MEMORY_CALLS)])
    state = BASE | {"tool_rounds": 0}
    result = asyncio.run(call_llm_node(
        state, llm=llm, rag_service=None, memory_store=StubMemoryStore(),
        bot_config=CONFIG_ON,
    ))
    assert result["messages"][0].tool_calls
    assert result["tool_rounds"] == 1


def test_returns_final_reply_when_no_tool_calls():
    llm = ScriptedLLM([AIMessage(content="最终回复")])
    state = BASE | {"tool_rounds": 0}
    result = asyncio.run(call_llm_node(
        state, llm=llm, rag_service=StubRagService(), memory_store=StubMemoryStore(),
        bot_config=CONFIG_ON,
    ))
    assert result["reply_text"] == "最终回复"
    assert "tool_rounds" not in result
    assert not result["messages"][0].tool_calls


def test_plain_path_when_no_services():
    llm = ScriptedLLM([AIMessage(content="普通")])
    result = asyncio.run(call_llm_node(BASE, llm=llm, rag_service=None, bot_config=CONFIG_ON))
    assert result["reply_text"] == "普通"


def test_plain_path_when_rounds_exhausted():
    llm = ScriptedLLM([AIMessage(content="耗尽后收尾")])
    state = BASE | {"tool_rounds": 1}
    config = BotConfig(rag_enabled=True, rag_max_agent_rounds=1)
    result = asyncio.run(call_llm_node(
        state, llm=llm, rag_service=StubRagService(), memory_store=StubMemoryStore(),
        bot_config=config,
    ))
    assert result["reply_text"] == "耗尽后收尾"
    assert not result["messages"][0].tool_calls


def test_memory_hint_injected_when_memory_enabled():
    llm = ScriptedLLM([AIMessage(content="好")])
    state = BASE | {"tool_rounds": 0}
    asyncio.run(call_llm_node(
        state, llm=llm, memory_store=StubMemoryStore(), bot_config=CONFIG_ON,
    ))
    assert any(
        "recall_user_memory" in getattr(m, "content", "")
        for m in llm.last_messages
    )
```

Run: `uv run pytest tests/test_call_llm_node.py -v`
Expected: FAIL（call_llm 尚未接 memory_store 参数，TypeError: unexpected keyword argument）

- [ ] **Step 4: 重写 call_llm.py**

整体替换 `bot/core/nodes/llm_node/call_llm.py`：

```python
import logging

from langchain_core.messages import AIMessage, SystemMessage
from langchain_openai import ChatOpenAI

from bot.core.tools import (
    TOOL_SCHEMA,
    TOOL_SCHEMA_RECALL,
    TOOL_SCHEMA_REMEMBER,
)
from common import BotConfig, MEMORY_TOOL_HINT
from object.bot.state import BotState

logger = logging.getLogger(__name__)


async def call_llm_node(
    state: BotState,
    llm: ChatOpenAI,
    rag_service=None,
    memory_store=None,
    bot_config: BotConfig | None = None,
) -> dict:
    """调用 LLM 生成回复。

    SystemMessages 每次调用动态构建、不持久化，人设始终位于 messages[0]。
    注入 rag_service / memory_store 时绑定对应工具：若 LLM 请求调用工具，
    返回原始 AIMessage（带 tool_calls），由 tool_node 执行并回环重入本节点；
    否则返回最终回复。轮次达到 rag_max_agent_rounds 上限后走无工具路径收尾。
    """
    persona = state["persona"].format(bot_name=state.get("bot_name", ""))
    system_msgs = [SystemMessage(content=persona)]

    summary = state.get("conversation_summary", "").strip()
    if summary:
        system_msgs.append(SystemMessage(content=f"之前的对话摘要：\n{summary}"))

    use_rag = rag_service is not None and rag_service.enabled
    use_memory = memory_store is not None
    if use_memory:
        system_msgs.append(SystemMessage(content=MEMORY_TOOL_HINT))

    messages = system_msgs + state["messages"]

    schemas = []
    if use_rag:
        schemas.append(TOOL_SCHEMA)
    if use_memory:
        schemas += [TOOL_SCHEMA_REMEMBER, TOOL_SCHEMA_RECALL]
    max_rounds = bot_config.rag_max_agent_rounds if bot_config is not None else 3
    rounds = state.get("tool_rounds", 0)

    if (use_rag or use_memory) and rounds < max_rounds:
        try:
            response = await llm.bind_tools(schemas).ainvoke(messages)
        except Exception as exc:
            _log_llm_error(exc, state.get("session_id", ""))
            return {
                "messages": [AIMessage(content="我暂时无法思考，请稍后再试")],
                "reply_text": "我暂时无法思考，请稍后再试",
            }
        if response.tool_calls:
            return {
                "messages": [response],
                "tool_rounds": rounds + 1,
                "reply_text": "",
            }
        return {
            "messages": [AIMessage(content=response.content)],
            "reply_text": response.content,
        }

    reply = await _invoke_plain(messages, llm, state)
    return {"messages": [AIMessage(content=reply)], "reply_text": reply}


async def _invoke_plain(messages: list, llm: ChatOpenAI, state: BotState) -> str:
    """无工具路径：一次 LLM 调用。"""
    try:
        response = await llm.ainvoke(messages)
        return response.content if hasattr(response, "content") else str(response)
    except Exception as exc:
        _log_llm_error(exc, state.get("session_id", ""))
        return "我暂时无法思考，请稍后再试"


def _log_llm_error(exc: Exception, session_id: str) -> None:
    if isinstance(exc, TimeoutError) or "Timeout" in type(exc).__name__:
        logger.warning("LLM call timed out for session %s", session_id)
    else:
        logger.exception("LLM call failed for session %s", session_id)
```

- [ ] **Step 5: handler.py ainvoke 键名同步**

`bot/handler.py` 第 178 行 `"rag_tool_rounds": 0,` → `"tool_rounds": 0,`。其余（user_memories、_extract_memories）Task 4 再删，本任务保持不动以免破坏调用链。

- [ ] **Step 6: 运行测试验证通过**

同步修改 `tests/test_graph.py` 第 35 行 `"rag_tool_rounds": 0,` → `"tool_rounds": 0,`（graph 层 ainvoke 的 state 键与计数器保持一致，否则集成测试拿不到真实计数）。

Run: `uv run pytest tests/ -v`
Expected: PASS（23 passed：call_llm 用例 4 → 6，21 − 4 + 6 = 23）

- [ ] **Step 7: Commit**

```bash
git add -A object/bot/state.py bot/core/nodes/llm_node common tests bot/handler.py
git commit -m "feat: call_llm 绑定记忆工具、移除 user_memories 注入，计数器改名 tool_rounds"
```

---

### Task 4: 清理图外抽取 + 全链路接线

**Files:**
- Modify: `object/bot/state.py`（删 `user_memories` 字段）
- Modify: `bot/core/graph.py`（memory_store 也传给 call_llm）
- Modify: `bot/handler.py`（删 `_extract_memories`/`format_memories`/`extract_llm`/`user_memories`，state 加 `user_id`）
- Modify: `main.py`
- Modify: `bot/core/memory.py`（删 `parse_extraction` 与 `EXTRACT_PROMPT` 引用）
- Modify: `common/prompts.py`（删 `EXTRACT_PROMPT`）、`common/__init__.py`
- Modify: `bot/core/utils/context.py`（`estimate_context_tokens` 去掉 memories 参数与层）
- Modify: `bot/core/nodes/action_node/summarize.py`（调用同步）
- Modify: `bot/core/tools/search_chat_history.py`（docstring `rag_tool_node` → `tool_node`）
- Modify: `tests/test_graph.py`（state 更新 + 记忆工具回环测试）、`tests/fakes.py`（`make_state` 去 `user_memories`）

**Interfaces:**
- Consumes: Task 2 的 `tool_node`、Task 3 的 `call_llm_node(state, llm, rag_service=None, memory_store=None, bot_config=None)`。
- Produces: `MessageHandler(client, graph, persona, api_client, rag_service=None)`（构造签名变化）；`create_graph(..., memory_store=...)` 同时注入 tool_node 与 call_llm。

- [ ] **Step 1: 删 user_memories 字段**

`object/bot/state.py` 删第 21 行 `user_memories: str`。

`tests/fakes.py` 的 `make_state` 删 `"user_memories": "",`。

- [ ] **Step 2: estimate_context_tokens 去 memories 层**

`bot/core/utils/context.py`：删 `memories: str` 参数、删 Layer 2 块（第 42-46 行），函数签名与注释同步：

```python
def estimate_context_tokens(
    messages: list[BaseMessage],
    persona: str,
    summary: str,
) -> int:
    """Estimate total tokens for the full context sent to the LLM.

    Builds the same layer structure that ``call_llm_node`` uses
    and passes it through ``count_tokens_approximately`` for a single
    consistent token count.
    """
    all_msgs: list[BaseMessage] = []

    # Layer 0: persona (always present)
    if persona.strip():
        all_msgs.append(SystemMessage(content=persona))

    # Layer 1: conversation summary (optional)
    if summary.strip():
        all_msgs.append(SystemMessage(
            content=f"之前的对话摘要：\n{summary}"
        ))

    # Layer 2..N: recent messages
    all_msgs.extend(messages)

    return count_tokens_approximately(
        all_msgs,
        chars_per_token=_CHARS_PER_TOKEN,
    )
```

`bot/core/nodes/action_node/summarize.py` 第 38-43 行调用改为：

```python
    total = estimate_context_tokens(
        state["messages"],
        state.get("persona", ""),
        state.get("conversation_summary", ""),
    )
```

- [ ] **Step 3: graph.py 把 memory_store 注入 call_llm**

`bot/core/graph.py` 第 32-34 行 call_llm partial 改为：

```python
    builder.add_node(
        "call_llm", partial(
            call_llm_node,
            llm=llm,
            rag_service=rag_service,
            memory_store=memory_store,
            bot_config=config,
        )
    )
```

- [ ] **Step 4: 删 EXTRACT_PROMPT**

`common/prompts.py` 删 `EXTRACT_PROMPT`（第 19-34 行）。`common/__init__.py` import 与 `__all__` 移除 `EXTRACT_PROMPT`。

`bot/core/memory.py`：删第 6 行 `from common import EXTRACT_PROMPT`、第 38 行 `EXTRACT_PROMPT = EXTRACT_PROMPT`、第 95-103 行 `parse_extraction` classmethod。`format_memories` 保留（store 的公共 API，虽暂未使用）。

- [ ] **Step 5: 清理 handler.py**

`bot/handler.py` 改动：
- 第 8 行删 `from bot.core.memory import MemoryStore`；第 5 行删 `from langchain_openai import ChatOpenAI`（不再有 extract_llm 类型标注）。
- 构造函数改为（删 `memory_store` 与 `extract_llm` 两个参数）：

```python
    def __init__(
        self,
        client: SatoriClient,
        graph: CompiledGraph,
        persona: str,
        api_client: SatoriApiClient,
        rag_service=None,
    ) -> None:
        self.client = client
        self.graph = graph
        self._persona = persona
        self._api_client = api_client
        self._rag_service = rag_service
        self._bot_id: str | None = None
        self._bot_name: str | None = None
        self._queue: asyncio.Queue[dict | None] = asyncio.Queue()
        self._locks: dict[str, asyncio.Lock] = {}
        self._worker_task: asyncio.Task[None] | None = None
```

- `_process()`：删第 151 行 `memories_text = self._memory_store.format_memories(user_id)`；ainvoke state 删 `"user_memories": memories_text,`、把 `"user_id": user_id,` 加进 state（`tool_rounds` 已在 Task 3 改好）；第 196 行删 `await self._extract_memories(user_id, raw_content, reply_text)`。
- 删整个 `_extract_memories` 方法（第 219-231 行）与「Memory extraction」节注释。

最终 `_process()` 关键段：

```python
        try:
            result = await self.graph.ainvoke(
                {
                    "new_message": HumanMessage(content=""),  # placeholder
                    "session_id": session_id,
                    "thread_id": thread_id,
                    "persona": self._persona,
                    "reply_text": "",
                    "should_respond": False,  # detect_intent decides
                    "bot_name": self._bot_name or "",
                    "bot_id": self._bot_id or "",
                    "tool_rounds": 0,
                    "user_id": user_id,
                    "channel_type": channel_type,
                    "raw_content": raw_content,
                    "user_name": user_name,
                },
                {
                    "configurable": {"thread_id": thread_id},
                    "recursion_limit": recursion_limit,
                },
            )
```

（`"tool_rounds": 0,` 已由 Task 3 改好，此处保持即可。）

- [ ] **Step 6: main.py 接线**

`main.py` 改动：
- `create_graph` 调用加 `memory_store=memory_store`，且 `memory_store` 在 handler 构造前创建：

```python
    rag_service = RagService(config) if config.rag_enabled else None
    memory_store = MemoryStore(db_dir=config.db_dir)
    graph, checkpointer = await create_graph(
        llm, config, db_dir=config.db_dir, rag_service=rag_service, memory_store=memory_store,
    )

    handler = MessageHandler(
        client, graph, persona, api_client, rag_service=rag_service,
    )
```

- `finally` 块加 `memory_store.close()`：

```python
    finally:
        await handler.stop()
        await client.disconnect()
        await api_client.close()
        if rag_service is not None:
            rag_service.close()
        memory_store.close()
        logger.info("Bye.")
```

- [ ] **Step 7: docstring 校正 + 图集成测试**

`bot/core/tools/search_chat_history.py` 第 4 行 docstring：`rag_tool_node` → `tool_node`。

`tests/test_graph.py`：`_initial_state()` 删 `"user_memories": "",`、第 35 行已是 `"tool_rounds": 0,`、加 `"user_id": "u1",`；末尾新增记忆工具回环测试：

```python
def test_graph_memory_tool_roundtrip(tmp_path):
    store = StubMemoryStore()
    store.store_memory("u1", "名字", "张三")
    llm = ScriptedLLM([
        # 第一次 call_llm：请求调用 recall 工具
        AIMessage(content="", tool_calls=[
            {"name": "recall_user_memory", "args": {"keyword": "名字"},
             "id": "call_m", "type": "tool_call"},
        ]),
        # 第二次 call_llm（回环后）：给出最终回复
        AIMessage(content="你之前说过你叫张三"),
    ])
    graph, _ = asyncio.run(
        create_graph(llm, BotConfig(rag_enabled=False), db_dir=str(tmp_path), memory_store=store)
    )

    result = asyncio.run(graph.ainvoke(_initial_state(), {"configurable": {"thread_id": "test:thread"}}))

    assert result["reply_text"] == "你之前说过你叫张三"
    tool_msgs = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert tool_msgs
    assert "张三" in tool_msgs[0].content
```

注意 `tests/test_graph.py` 顶部 import 加 `from tests.fakes import StubMemoryStore`。

- [ ] **Step 8: 全量测试**

Run: `uv run pytest tests/ -v`
Expected: PASS（24 passed = 23 + 新增记忆回环 1）

- [ ] **Step 9: 导入冒烟**

Run: `uv run python -c "from bot.core.graph import create_graph; from bot.handler import MessageHandler; from bot.core.tools import TOOL_SCHEMA_REMEMBER, TOOL_SCHEMA_RECALL; from bot.core.nodes.tool_node import tool_node; print('imports ok')"`
Expected: `imports ok`

- [ ] **Step 10: Commit**

```bash
git add -A object/bot/state.py bot/core common tests main.py
git commit -m "feat: 记忆改为工具驱动，移除图外抽取链路（extract_llm/EXTRACT_PROMPT/format_memories 调用）"
```

---

### Task 5: CLAUDE.md 同步 + 全量回归

**Files:**
- Modify: `CLAUDE.md`

**Interfaces:**
- 无代码接口。同步文档以反映工具化记忆与 tool_node 分发器。

- [ ] **Step 1: 更新 CLAUDE.md**

- **架构树**：`bot/core/tools/` 下加 `user_memory.py`；`tool_node/` 下 `rag_tool_node.py` → `tool_node.py`。
- **数据流**：删除 `extract memories via MemoryStore` 一行；`tool_node` 行改为「按工具名分发（search_chat_history / remember_user_memory / recall_user_memory），回环到 call_llm」。
- **SystemMessage injection**：第 2 层 `user_memories (from MemoryStore)` 改为「memory tools usage hint（`MEMORY_TOOL_HINT`，仅注入 memory_store 时）」。
- **新增「记忆工具」段**（仿 RAG 段）：工具在 `bot/core/tools/user_memory.py`，执行节点 `tool_node` 分发；`user_id` 从 state 注入；移除进图前全量注入与图外抽取；LLM 主动 remember/recall；同步 sqlite 用 `asyncio.to_thread`。
- **RAG 段**：提及 `call_llm` 绑定工具列表现在包含记忆工具（若开启），`tool_rounds` 为总轮数计数。
- **Node type convention**：`tool_node/` 描述改为「按工具名分发的通用工具节点（RAG 检索 + 用户记忆存取）」。
- **Gotchas 工具定位**：`rag_tool_node` → `tool_node`；`memory_store` 经 `functools.partial` 注入 tool_node 与 call_llm；`MemoryStore` 表仍 `db/memory.sqlite`。
- **Three-database 表**：`memory.sqlite` 用途行从「extracted by LLM」改为「LLM 经 remember/recall 工具写入」。

- [ ] **Step 2: 全量回归**

Run: `uv run pytest tests/ -v`
Expected: PASS（24 passed）

Run: `uv run python -c "from bot.core.graph import create_graph; print('imports ok')"`
Expected: `imports ok`

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: CLAUDE.md 同步记忆工具驱动与 tool_node 分发器"
```
