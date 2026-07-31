# RAG 工具节点化重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `search_chat_history` RAG 工具迁到 `bot/core/tools/`，用图级 `tool_node` 取代 `call_llm` 内部的 ReAct 循环，接入主图。

**Architecture:** 标准 LangGraph agent 模式——`call_llm` 绑定工具并返回带 `tool_calls` 的原始 AIMessage，条件边路由到 `tool_node` 执行工具，回边到 `call_llm` 继续，直到无 `tool_calls`。轮次上限用 state 计数器 `rag_tool_rounds` 控制，耗尽后走无工具路径收尾。工具调用消息持久化到 checkpoint。

**Tech Stack:** Python 3.12 / uv（清华镜像）/ LangGraph / langchain-core / pytest（新增 dev 依赖）

## Global Constraints

- 自定义 tool_node，**不使用** `langgraph.prebuilt.ToolNode`（`thread_id` 需从 state 注入，prebuilt 只能从 tool_call args 取参）
- 工具调用消息（带 tool_calls 的 AIMessage + ToolMessage）**持久化到 checkpoint**（用户已确认的行为变更）
- 轮次上限用 state 字段 `rag_tool_rounds`，不用 `recursion_limit`
- 错误语义保持：工具失败 → ToolMessage `"检索历史消息失败。"`；LLM 失败 → `"我暂时无法思考，请稍后再试"`
- 旧 `bot/core/rag/tools.py` 删除，`make_search_tool` 工厂被模块级 `search_chat_history(query, rag_service, thread_id)` 取代
- `rag_tool_node` 通过 `functools.partial` 注入 `rag_service`（与现有节点注入模式一致）
- 每任务结束提交一次

---

### Task 1: pytest 脚手架 + 迁移工具到 `bot/core/tools/`

**Files:**
- Create: `tests/__init__.py`（空文件）
- Create: `tests/fakes.py`
- Create: `tests/test_search_chat_history.py`
- Create: `bot/core/tools/search_chat_history.py`
- Modify: `bot/core/tools/__init__.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: `bot.core.rag.service.RagService.search(query, thread_id, top_k=None, score_threshold=None)`（已有）
- Produces:
  - `search_chat_history(query: str, rag_service, thread_id: str) -> str` —— 检索并格式化为 ToolMessage 文本
  - `TOOL_SCHEMA`（`bot.core.tools` 导出，Task 3 的 `call_llm` 使用）
  - 测试桩：`ScriptedLLM`、`StubRagService`、`make_state()`（`tests.fakes` 导出，Task 2/3/4 复用）

- [ ] **Step 1: 安装 pytest 并配置**

```bash
uv add --dev pytest
```

在 `pyproject.toml` 末尾追加：

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
```

- [ ] **Step 2: 创建测试脚手架**

创建 `tests/__init__.py`（空文件）。

创建 `tests/fakes.py`：

```python
"""测试桩：脚本化 LLM、Stub RAG 服务、最小图状态工厂。

ScriptedLLM.bind_tools 返回 self —— ainvoke 忽略工具 schema、按序弹出
脚本消息，因此工具绑定路径与普通路径共用同一条消息队列。
"""

from langchain_core.messages import AIMessage


class ScriptedLLM:
    """按序返回脚本响应的假 LLM。"""

    def __init__(self, responses: list[AIMessage]):
        self._responses = list(responses)
        self._index = 0

    def bind_tools(self, tools, **kwargs):
        return self

    async def ainvoke(self, messages, **kwargs):
        if self._index >= len(self._responses):
            raise AssertionError("ScriptedLLM exhausted: no more scripted responses")
        msg = self._responses[self._index]
        self._index += 1
        return msg


class StubRagService:
    """假 RagService：enabled 开关 + 脚本化检索结果。"""

    def __init__(self, enabled=True, search_results=None, raise_on_search=False):
        self.enabled = enabled
        self.search_results = search_results or []
        self.raise_on_search = raise_on_search
        self.last_query = None
        self.last_thread_id = None

    async def search(self, query, thread_id, top_k=None, score_threshold=None):
        self.last_query = query
        self.last_thread_id = thread_id
        if self.raise_on_search:
            raise RuntimeError("search failed")
        return self.search_results


def make_state(**overrides) -> dict:
    """构造最小图状态，供节点单元测试使用。"""
    state = {
        "messages": [],
        "persona": "你是{bot_name}",
        "user_memories": "",
        "conversation_summary": "",
        "session_id": "test:session",
        "thread_id": "test:thread",
        "new_message": None,
        "reply_text": "",
        "should_respond": True,
        "bot_name": "测试机器人",
        "channel_type": 0,
        "bot_id": "bot1",
        "raw_content": "你好",
        "user_name": "张三",
        "rag_tool_rounds": 0,
    }
    state.update(overrides)
    return state
```

- [ ] **Step 3: 写失败测试**

创建 `tests/test_search_chat_history.py`：

```python
import asyncio

from bot.core.tools import TOOL_SCHEMA, search_chat_history
from bot.core.tools.search_chat_history import _format_results
from tests.fakes import StubRagService

SAMPLE = [
    {
        "thread_id": "g", "user_id": "u1", "user_name": "张三",
        "content": "之前决定用 qwen3-embedding 做嵌入", "role": "user",
        "timestamp": 1753910400, "score": 0.9,
    },
]


def test_tool_schema_exposes_query_param():
    fn = TOOL_SCHEMA["function"]
    assert fn["name"] == "search_chat_history"
    assert "query" in fn["parameters"]["properties"]


def test_format_results_renders_speaker_and_content():
    text = _format_results(SAMPLE)
    assert "张三" in text
    assert "之前决定用 qwen3-embedding 做嵌入" in text


def test_format_results_empty():
    assert _format_results([]) == "没有找到相关的历史消息。"


def test_search_chat_history_returns_formatted_text():
    rag = StubRagService(search_results=SAMPLE)
    text = asyncio.run(search_chat_history("嵌入模型", rag, "test:thread"))
    assert "之前决定用 qwen3-embedding 做嵌入" in text
    assert rag.last_query == "嵌入模型"
    assert rag.last_thread_id == "test:thread"
```

- [ ] **Step 4: 运行测试确认失败**

Run: `uv run pytest tests/test_search_chat_history.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'bot.core.tools'`（`bot/core/tools/search_chat_history.py` 尚不存在）

- [ ] **Step 5: 实现工具模块**

创建 `bot/core/tools/search_chat_history.py`：

```python
"""search_chat_history 工具。

纯函数：按查询检索群聊历史并格式化为上下文文本块。
rag_service 与 thread_id 由 rag_tool_node 在调用时注入，LLM 无需知道内部标识。
"""

import logging
import time

from bot.core.rag.service import RagService

logger = logging.getLogger(__name__)

TOOL_NAME = "search_chat_history"

TOOL_DESCRIPTION = (
    "检索群聊历史消息中与给定问题最相关的记录。"
    "当用户询问之前讨论过的话题、事实、决定、约定或个人偏好时使用，"
    "以获取准确的历史上下文进行回复。"
)

TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": TOOL_DESCRIPTION,
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "要检索的问题或关键词，用中文表述",
                },
            },
            "required": ["query"],
        },
    },
}


def _format_time(ts: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def _format_results(results: list[dict]) -> str:
    if not results:
        return "没有找到相关的历史消息。"
    lines = []
    for r in results:
        speaker = r["user_name"] or ("我" if r["role"] == "assistant" else r["role"])
        lines.append(f"[{_format_time(r['timestamp'])}] {speaker}: {r['content']}")
    return "\n".join(lines)


async def search_chat_history(query: str, rag_service: RagService, thread_id: str) -> str:
    """检索并格式化群聊历史，返回适合作为 ToolMessage 的文本。"""
    results = await rag_service.search(query, thread_id)
    return _format_results(results)
```

修改 `bot/core/tools/__init__.py`：

```python
from .search_chat_history import TOOL_NAME, TOOL_SCHEMA, search_chat_history

__all__ = ["TOOL_NAME", "TOOL_SCHEMA", "search_chat_history"]
```

- [ ] **Step 6: 运行测试确认通过**

Run: `uv run pytest tests/test_search_chat_history.py -v`
Expected: PASS（4 passed）

- [ ] **Step 7: 提交**

```bash
git add tests/ pyproject.toml bot/core/tools/ uv.lock
git commit -m "test: 添加 pytest 脚手架与 search_chat_history 测试，工具迁入 bot/core/tools"
```

---

### Task 2: 新增 `rag_tool_node` 工具节点

**Files:**
- Create: `bot/core/nodes/tool_node/rag_tool_node.py`
- Modify: `bot/core/nodes/tool_node/__init__.py`
- Modify: `bot/core/nodes/__init__.py`
- Create: `tests/test_rag_tool_node.py`

**Interfaces:**
- Consumes: `search_chat_history(query, rag_service, thread_id)`（Task 1）
- Produces: `rag_tool_node(state: BotState, rag_service=None) -> dict`（Task 4 的 graph 注册）

- [ ] **Step 1: 写失败测试**

创建 `tests/test_rag_tool_node.py`：

```python
import asyncio

from langchain_core.messages import AIMessage, ToolMessage

from bot.core.nodes.tool_node import rag_tool_node
from tests.fakes import StubRagService, make_state

TOOL_CALL_MSG = AIMessage(content="", tool_calls=[
    {"name": "search_chat_history", "args": {"query": "之前聊了什么"},
     "id": "call_1", "type": "tool_call"},
])

SAMPLE = [
    {"thread_id": "test:thread", "user_id": "u1", "user_name": "张三",
     "content": "之前聊了 RAG", "role": "user", "timestamp": 1753910400,
     "score": 0.8},
]


def test_rag_tool_node_executes_tool_call():
    rag = StubRagService(search_results=SAMPLE)
    state = make_state(messages=[TOOL_CALL_MSG])
    result = asyncio.run(rag_tool_node(state, rag_service=rag))

    msgs = result["messages"]
    assert isinstance(msgs[0], ToolMessage)
    assert msgs[0].tool_call_id == "call_1"
    assert "之前聊了 RAG" in msgs[0].content
    assert rag.last_thread_id == "test:thread"


def test_rag_tool_node_noop_without_tool_calls():
    state = make_state(messages=[AIMessage(content="普通回复")])
    result = asyncio.run(rag_tool_node(state, rag_service=StubRagService()))
    assert result == {}


def test_rag_tool_node_degrades_on_search_error():
    rag = StubRagService(raise_on_search=True)
    state = make_state(messages=[TOOL_CALL_MSG])
    result = asyncio.run(rag_tool_node(state, rag_service=rag))
    assert result["messages"][0].content == "检索历史消息失败。"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_rag_tool_node.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'bot.core.nodes.tool_node.rag_tool_node'`

- [ ] **Step 3: 实现节点**

创建 `bot/core/nodes/tool_node/rag_tool_node.py`：

```python
"""rag_tool_node — 执行 call_llm 请求的工具调用。

读取 state 最后一条消息的 tool_calls，逐条执行 search_chat_history，
把结果以 ToolMessage 写回 state。失败仅降级为占位文案，不中断对话。
"""

import logging

from langchain_core.messages import ToolMessage

from bot.core.tools import search_chat_history
from object.bot.state import BotState

logger = logging.getLogger(__name__)


async def rag_tool_node(state: BotState, rag_service=None) -> dict:
    messages = state.get("messages") or []
    last = messages[-1] if messages else None
    tool_calls = getattr(last, "tool_calls", None) or []
    if not tool_calls:
        return {}

    thread_id = state.get("thread_id", "")
    tool_messages = []
    for tc in tool_calls:
        query = (tc.get("args") or {}).get("query", "")
        try:
            content = await search_chat_history(query, rag_service, thread_id)
        except Exception:
            logger.exception(
                "Tool search_chat_history failed for session %s",
                state.get("session_id", ""),
            )
            content = "检索历史消息失败。"
        tool_messages.append(ToolMessage(content=content, tool_call_id=tc["id"]))
    return {"messages": tool_messages}
```

修改 `bot/core/nodes/tool_node/__init__.py`：

```python
from .rag_tool_node import rag_tool_node

__all__ = ["rag_tool_node"]
```

修改 `bot/core/nodes/__init__.py`：

```python
from .action_node import detect_intent, summarize_node
from .llm_node import call_llm_node, router_node
from .tool_node import rag_tool_node

__all__ = ["call_llm_node", "detect_intent", "rag_tool_node", "router_node", "summarize_node"]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_rag_tool_node.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add bot/core/nodes/ tests/test_rag_tool_node.py
git commit -m "feat: 添加 rag_tool_node 工具节点"
```

---

### Task 3: `call_llm` 去除内部 ReAct 循环

**Files:**
- Modify: `bot/core/nodes/llm_node/call_llm.py`（整体重写）
- Create: `tests/test_call_llm_node.py`

**Interfaces:**
- Consumes: `TOOL_SCHEMA`（Task 1）
- Produces: 新的 `call_llm_node(state, llm, rag_service=None, bot_config=None) -> dict` 行为——
  - 有 tool_calls → `{"messages": [response], "rag_tool_rounds": rounds+1, "reply_text": ""}`
  - 无 tool_calls → `{"messages": [AIMessage(content=...)], "reply_text": ...}`
  - 轮次耗尽或 rag 禁用 → 走 `_invoke_plain` 无工具路径

- [ ] **Step 1: 写失败测试**

创建 `tests/test_call_llm_node.py`：

```python
import asyncio

from langchain_core.messages import AIMessage, HumanMessage

from bot.core.nodes import call_llm_node
from common import BotConfig
from tests.fakes import ScriptedLLM, StubRagService, make_state

TOOL_CALLS = [
    {"name": "search_chat_history", "args": {"query": "x"}, "id": "call_1", "type": "tool_call"},
]
BASE = make_state(messages=[HumanMessage(content="你好")])
CONFIG_ON = BotConfig(rag_enabled=True)


def test_returns_tool_calls_when_rag_enabled():
    llm = ScriptedLLM([AIMessage(content="", tool_calls=TOOL_CALLS)])
    state = BASE | {"rag_tool_rounds": 0}
    result = asyncio.run(call_llm_node(state, llm=llm, rag_service=StubRagService(), bot_config=CONFIG_ON))
    assert result["reply_text"] == ""
    assert result["rag_tool_rounds"] == 1
    assert result["messages"][0].tool_calls


def test_returns_final_reply_when_no_tool_calls():
    llm = ScriptedLLM([AIMessage(content="最终回复")])
    state = BASE | {"rag_tool_rounds": 0}
    result = asyncio.run(call_llm_node(state, llm=llm, rag_service=StubRagService(), bot_config=CONFIG_ON))
    assert result["reply_text"] == "最终回复"
    assert "rag_tool_rounds" not in result
    assert not result["messages"][0].tool_calls


def test_plain_path_when_rag_disabled():
    llm = ScriptedLLM([AIMessage(content="普通")])
    result = asyncio.run(call_llm_node(BASE, llm=llm, rag_service=None, bot_config=CONFIG_ON))
    assert result["reply_text"] == "普通"


def test_plain_path_when_rounds_exhausted():
    llm = ScriptedLLM([AIMessage(content="耗尽后收尾")])
    state = BASE | {"rag_tool_rounds": 1}
    config = BotConfig(rag_enabled=True, rag_max_agent_rounds=1)
    result = asyncio.run(call_llm_node(state, llm=llm, rag_service=StubRagService(), bot_config=config))
    assert result["reply_text"] == "耗尽后收尾"
    assert not result["messages"][0].tool_calls
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_call_llm_node.py -v`
Expected: FAIL（当前 `call_llm` 返回 `AIMessage(content=reply)` 且不设 `rag_tool_rounds` / 不返回原始 tool_calls）

- [ ] **Step 3: 重写 `call_llm.py`**

用以下内容整体替换 `bot/core/nodes/llm_node/call_llm.py`：

```python
import logging

from langchain_core.messages import AIMessage, SystemMessage
from langchain_openai import ChatOpenAI

from bot.core.tools import TOOL_SCHEMA
from common import BotConfig
from object.bot.state import BotState

logger = logging.getLogger(__name__)


async def call_llm_node(
    state: BotState,
    llm: ChatOpenAI,
    rag_service=None,
    bot_config: BotConfig | None = None,
) -> dict:
    """调用 LLM 生成回复。

    SystemMessages 每次调用动态构建、不持久化，人设始终位于 messages[0]。

    当注入 rag_service 且启用时，绑定 search_chat_history 工具：若 LLM
    请求调用工具，返回原始 AIMessage（带 tool_calls），由 tool_node 执行并
    回环重入本节点；否则返回最终回复。轮次达到 rag_max_agent_rounds 上限
    后走无工具路径强制收尾。
    """
    persona = state["persona"].format(bot_name=state.get("bot_name", ""))
    system_msgs = [SystemMessage(content=persona)]

    summary = state.get("conversation_summary", "").strip()
    if summary:
        system_msgs.append(SystemMessage(content=f"之前的对话摘要：\n{summary}"))

    memories = state.get("user_memories", "").strip()
    if memories:
        system_msgs.append(SystemMessage(content=f"关于当前用户已知的信息：\n{memories}"))

    messages = system_msgs + state["messages"]

    use_rag = rag_service is not None and rag_service.enabled
    max_rounds = bot_config.rag_max_agent_rounds if bot_config is not None else 3
    rounds = state.get("rag_tool_rounds", 0)

    if use_rag and rounds < max_rounds:
        try:
            response = await llm.bind_tools([TOOL_SCHEMA]).ainvoke(messages)
        except Exception as exc:
            _log_llm_error(exc, state.get("session_id", ""))
            return {
                "messages": [AIMessage(content="我暂时无法思考，请稍后再试")],
                "reply_text": "我暂时无法思考，请稍后再试",
            }
        if response.tool_calls:
            return {
                "messages": [response],
                "rag_tool_rounds": rounds + 1,
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

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_call_llm_node.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: 提交**

```bash
git add bot/core/nodes/llm_node/call_llm.py tests/test_call_llm_node.py
git commit -m "refactor: call_llm 去除内部 ReAct 循环，改为图级工具调用"
```

---

### Task 4: 主图接入 `tool_node`

**Files:**
- Modify: `bot/core/graph.py`
- Modify: `object/bot/state.py`
- Modify: `bot/handler.py`
- Create: `tests/test_graph.py`

**Interfaces:**
- Consumes: `rag_tool_node`（Task 2）、新 `call_llm_node`（Task 3）
- Produces: 新图结构 `detect_intent → router → call_llm → (tool_node → call_llm) | summarize → END`；`BotState` 增加 `rag_tool_rounds`；handler 初始 state 传入 `rag_tool_rounds: 0`

- [ ] **Step 1: 写失败集成测试**

创建 `tests/test_graph.py`：

```python
import asyncio

from langchain_core.messages import AIMessage, HumanMessage

from bot.core.graph import create_graph
from common import BotConfig
from tests.fakes import ScriptedLLM, StubRagService

TOOL_CALLS = [
    {"name": "search_chat_history", "args": {"query": "之前聊了什么"}, "id": "call_1", "type": "tool_call"},
]

SAMPLE = [
    {"thread_id": "test:thread", "user_id": "u1", "user_name": "张三",
     "content": "上次我们决定用 qwen3-embedding", "role": "user",
     "timestamp": 1753910400, "score": 0.85},
]


def _initial_state() -> dict:
    # channel_type=1 (DIRECT) → detect_intent 直接置 should_respond=True，router 不消耗脚本消息
    return {
        "new_message": HumanMessage(content="还记得我们聊过 RAG 吗？"),
        "session_id": "test:session",
        "thread_id": "test:thread",
        "persona": "你是{bot_name}",
        "user_memories": "",
        "reply_text": "",
        "should_respond": False,
        "bot_name": "测试机器人",
        "bot_id": "bot1",
        "channel_type": 1,
        "raw_content": "还记得我们聊过 RAG 吗？",
        "user_name": "张三",
        "rag_tool_rounds": 0,
    }


def test_graph_loops_tool_call_then_answers(tmp_path):
    rag = StubRagService(search_results=SAMPLE)
    llm = ScriptedLLM([
        # 第一次 call_llm：请求调用工具
        AIMessage(content="", tool_calls=TOOL_CALLS),
        # 第二次 call_llm（回环后）：给出最终回复
        AIMessage(content="我们上次决定用 qwen3-embedding 做嵌入"),
    ])
    graph, _ = asyncio.run(
        create_graph(llm, BotConfig(rag_enabled=True), db_dir=str(tmp_path), rag_service=rag)
    )

    result = asyncio.run(graph.ainvoke(_initial_state(), {"configurable": {"thread_id": "test:thread"}}))

    assert result["reply_text"] == "我们上次决定用 qwen3-embedding 做嵌入"
    # 循环确实发生：state 中应包含 ToolMessage
    assert any(type(m).__name__ == "ToolMessage" for m in result["messages"])
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_graph.py -v`
Expected: FAIL —— 当前图中无 `tool_node` 节点 / 无回边，工具调用 AIMessage 不会被执行（断言 `ToolMessage in messages` 失败）

- [ ] **Step 3: 修改图与状态**

修改 `bot/core/graph.py`：

将顶部 import（第 11 行）：
```python
from bot.core.nodes import call_llm_node, detect_intent, router_node, summarize_node
```
改为：
```python
from bot.core.nodes import call_llm_node, detect_intent, rag_tool_node, router_node, summarize_node
```

将 `create_graph` 中从 `builder.add_node("summarize", ...)` 到 `builder.add_edge("summarize", END)` 的整个块：

```python
    builder.add_node("summarize", partial(summarize_node, llm=llm, bot_config=config))

    builder.add_edge(START, "detect_intent")
    builder.add_edge("detect_intent", "router")
    builder.add_conditional_edges(
        "router",
        lambda s: "call_llm" if s.get("should_respond", True) else END,
    )
    builder.add_edge("call_llm", "summarize")   # always run (node handles skip)
    builder.add_edge("summarize", END)
```

改为：

```python
    builder.add_node("summarize", partial(summarize_node, llm=llm, bot_config=config))
    builder.add_node("tool_node", partial(rag_tool_node, rag_service=rag_service))

    builder.add_edge(START, "detect_intent")
    builder.add_edge("detect_intent", "router")
    builder.add_conditional_edges(
        "router",
        lambda s: "call_llm" if s.get("should_respond", True) else END,
    )
    builder.add_conditional_edges(
        "call_llm",
        lambda s: "tool_node" if getattr(s["messages"][-1], "tool_calls", None) else "summarize",
    )
    builder.add_edge("tool_node", "call_llm")
    builder.add_edge("summarize", END)
```

修改 `object/bot/state.py`，在 `bot_name: str` 行后加字段：

```python
    bot_name: str
    rag_tool_rounds: int   # RAG 工具调用轮次计数（call_llm 递增，工具回环上限）
```

修改 `bot/handler.py` 的 `graph.ainvoke` 初始 state dict（`"bot_id": self._bot_id or "",` 行后）加：

```python
                    "rag_tool_rounds": 0,
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/ -v`
Expected: PASS（4 + 3 + 4 + 1 = 12 passed）

- [ ] **Step 5: 提交**

```bash
git add bot/core/graph.py object/bot/state.py bot/handler.py tests/test_graph.py
git commit -m "feat: 主图接入 tool_node，BotState 与 handler 增加 rag_tool_rounds"
```

---

### Task 5: 清理旧工具 + 更新文档

**Files:**
- Delete: `bot/core/rag/tools.py`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: 全部既有变更

- [ ] **Step 1: 删除旧工具文件**

```bash
git rm bot/core/rag/tools.py
```

- [ ] **Step 2: 确认无残留引用**

Run: `grep -rn "make_search_tool\|rag\.tools" bot/ tests/`
Expected: 无输出（0 匹配）

- [ ] **Step 3: 全量测试 + 导入冒烟**

Run: `uv run pytest tests/ -v`
Expected: PASS（12 passed）

Run: `uv run python -c "from bot import create_graph; from bot.core.tools import TOOL_SCHEMA, search_chat_history; from bot.core.nodes import rag_tool_node; print('imports ok')"`
Expected: `imports ok`

- [ ] **Step 4: 更新 CLAUDE.md**

更新以下段落：

1. **架构树**——`core/tools/` 行改为：
   ```
       tools/                   # Tool definitions imported by graph / tool_node / subgraph
         search_chat_history.py #   search_chat_history 工具（TOOL_SCHEMA + 纯函数）
   ```
   `core/nodes/` 下 `tool_node/` 行改为：
   ```
       tool_node/               #   rag_tool_node — 执行 LLM 请求的工具调用（图级循环）
   ```
   移除架构树中 `rag/` 块末尾的 `tools.py` 一行，改为：
   ```
       rag/                     # 群聊历史 RAG（向量检索）
         embedder.py            #   EmbeddingService — Ollama qwen3-embedding，Instruct 前缀
         service.py             #   RagService — index_turn / search 组合接口
         store.py               #   RagVectorStore — sqlite-vec 向量表 + 元数据表 (rag.sqlite)
   ```

2. **数据流**——`call_llm` 行与 `summarize` 行之间插入：
   ```
       tool_node (tool_node)    ← call_llm 返回 tool_calls 时执行 search_chat_history，回环到 call_llm
   ```

3. **RAG 段落**——重写"触发"与"工具闭环"两节：

   - 触发改为：
     > 注入 `RagService` 后，`call_llm` 绑定 `search_chat_history` 工具，**LLM 自行决定何时检索**。若返回 `tool_calls`，条件边路由到 `tool_node` 执行，回边到 `call_llm` 继续；轮次达到 `rag_max_agent_rounds` 后走无工具路径强制收尾。
   - 工具闭环改为：
     > `search_chat_history(query, rag_service, thread_id)` 是纯函数，`rag_tool_node` 从 state 注入 `thread_id` 与 `rag_service`；工具调用消息（AIMessage + ToolMessage）持久化到 checkpoint。

4. **Node type convention**——`tool_node/` 行从"future"改为实际节点：
   ```
   - **`tool_node/`** — tools invoked by LLM via function calling（`rag_tool_node` 执行 `search_chat_history`，经条件边回环）
   ```
   删除该节末尾的 `> Note: the RAG tool factory lives in bot/core/rag/tools.py ...` 引用（文件已删除）。

5. **SystemMessage injection** 节——checkpoint 保证行改为：
   > Checkpoint stores conversation history (HumanMessage + AIMessage + ToolMessage)，not system instructions

6. **Gotchas** 的 sqlite-vec 行保留；新增一行：
   > **工具定位**: RAG 工具在 `bot/core/tools/search_chat_history.py`，执行节点在 `bot/core/nodes/tool_node/rag_tool_node.py`。工具调用消息会持久化到 checkpoint（不同于 SystemMessage）。

- [ ] **Step 5: 提交**

```bash
git add bot/core/rag/ CLAUDE.md
git commit -m "chore: 删除 rag/tools.py，更新 CLAUDE.md 反映图级 tool_node"
```

---
