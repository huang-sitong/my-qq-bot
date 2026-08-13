# MCP 外部工具接入 + ToolNode 迁移实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把工具执行从自定义 `tool_node` 迁移到 `langgraph.prebuilt.ToolNode`，并接入 Tavily 官方远程 MCP 端点实现 web search（第一个 MCP server）。

**Architecture:** 所有工具（RAG 检索、用户记忆、MCP 外部工具）归一为 `BaseTool` 列表，由 `bot/core/tools/factory.py::build_tools` 组装、`ToolNode` 统一执行。内部工具的 `thread_id`/`user_id` 经 `InjectedState` 从图 state 按次注入（已核实 langgraph 1.2.2：`tool_call_schema` 自动排除注入参数、ToolNode 执行时注入），服务依赖经闭包绑定。MCP 工具经 `MultiServerMCPClient` 加载，每次调用自建 session，零生命周期管理。

**Tech Stack:** Python 3.12、uv、langgraph 1.2.2、langchain-core 1.4.9、`mcp` + `langchain-mcp-adapters`（>=0.3.1，本计划新增）

## Global Constraints

- 包管理器 `uv`，PyPI 镜像 `https://pypi.tuna.tsinghua.edu.cn/simple`（pyproject 已配置）。新增依赖用 `uv add`。
- 测试命令：`uv run pytest tests/<file>.py -v`；运行全部：`uv run pytest -q`。
- **Tavily MCP 只用官方远程端点** `https://mcp.tavily.com/mcp/?tavilyApiKey=<key>`（`transport: "streamable_http"`）。禁止使用 PyPI 的 `tavily-mcp` 社区包。
- 内部工具失败降级为占位文案 `"工具执行失败。"`，真实错误记日志，原始异常不进 checkpoint（产品决策，勿改）。
- 每个任务结束时**全量测试** `uv run pytest tests/ -q` 全绿，再 commit。提交信息用中文，遵循仓库既有风格（`feat:`/`refactor:`/`test:`/`docs:`）。
- 迁移后删除所有手写 `TOOL_SCHEMA` 裸 dict；schema 由函数签名 + `description` 推断（单一来源）。
- `InjectedState` 从 `langgraph.prebuilt` 导入；`Annotated` 从 `typing` 导入。

---

### Task 1: MCP 依赖 + BotConfig 配置字段

**Files:**
- Modify: `pyproject.toml`（经 `uv add`）
- Modify: `common/config.py`（文件顶部加 `import json`、`import logging`）
- Test: `tests/test_mcp_config.py`

**Interfaces:**
- Produces: `BotConfig.mcp_enabled: bool`、`BotConfig.mcp_servers: dict`、`BotConfig.mcp_tool_name_prefix: bool`、`BotConfig.tavily_api_key: str`；方法 `BotConfig.mcp_server_connections() -> dict[str, dict]`（自动注册 `tavily` server）

- [ ] **Step 1: 写失败测试 `tests/test_mcp_config.py`**

```python
"""BotConfig MCP 配置字段测试。"""

from common import BotConfig


def test_mcp_disabled_by_default(monkeypatch):
    monkeypatch.delenv("BOT_MCP_ENABLED", raising=False)
    cfg = BotConfig()
    assert cfg.mcp_enabled is False


def test_mcp_enabled_flag(monkeypatch):
    monkeypatch.setenv("BOT_MCP_ENABLED", "1")
    cfg = BotConfig()
    assert cfg.mcp_enabled is True


def test_tavily_connection_auto_registered_when_key_set():
    cfg = BotConfig(tavily_api_key="sk-test")
    servers = cfg.mcp_server_connections()
    assert "tavily" in servers
    assert servers["tavily"]["transport"] == "streamable_http"
    assert servers["tavily"]["url"].startswith("https://mcp.tavily.com/mcp/?")
    assert "sk-test" in servers["tavily"]["url"]


def test_no_tavily_without_key():
    cfg = BotConfig(tavily_api_key="  ")
    servers = cfg.mcp_server_connections()
    assert "tavily" not in servers


def test_extra_servers_from_env_json(monkeypatch):
    monkeypatch.setenv(
        "BOT_MCP_SERVERS",
        '{"weather": {"transport": "streamable_http", "url": "http://localhost:8000/mcp"}}',
    )
    cfg = BotConfig()
    assert cfg.mcp_servers["weather"]["url"] == "http://localhost:8000/mcp"


def test_invalid_mcp_servers_json_degrades(monkeypatch):
    monkeypatch.setenv("BOT_MCP_SERVERS", "{not json")
    cfg = BotConfig()
    assert cfg.mcp_servers == {}


def test_extra_servers_merge_with_tavily():
    cfg = BotConfig(
        mcp_servers={
            "weather": {"transport": "streamable_http", "url": "http://localhost:8000/mcp"},
        },
        tavily_api_key="sk-test",
    )
    servers = cfg.mcp_server_connections()
    assert set(servers) == {"weather", "tavily"}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_mcp_config.py -v`
Expected: FAIL —— `AttributeError`（`BotConfig` 还没有这些字段）

- [ ] **Step 3: 加依赖**

Run: `uv add mcp langchain-mcp-adapters`

- [ ] **Step 4: 实现配置**

在 `common/config.py` 顶部补 import（`import os` 已存在）：

```python
import json
import logging

logger = logging.getLogger(__name__)
```

在模块顶层（`BotConfig` 类定义**之前**）加辅助函数：

```python
def _load_mcp_servers() -> dict:
    """解析 BOT_MCP_SERVERS JSON；非法 JSON 或非 dict 降级为空。"""
    raw = os.getenv("BOT_MCP_SERVERS", "{}")
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        logger.warning("BOT_MCP_SERVERS 非法 JSON，忽略：%s", raw[:200])
        return {}
```

在 `BotConfig` 的 `vision_timeout` 字段后追加字段：

```python
    # --- MCP (外部工具，经 langchain-mcp-adapters 接入) ---
    mcp_enabled: bool = field(
        default_factory=lambda: os.getenv("BOT_MCP_ENABLED", "0") not in ("0", "false", "False", ""),
    )
    mcp_servers: dict = field(default_factory=_load_mcp_servers)
    mcp_tool_name_prefix: bool = field(
        default_factory=lambda: os.getenv("BOT_MCP_TOOL_NAME_PREFIX", "0") not in ("0", "false", "False", ""),
    )
    tavily_api_key: str = field(
        default_factory=lambda: os.getenv("TAVILY_API_KEY", ""),
    )

    def mcp_server_connections(self) -> dict:
        """MCP server 连接配置：额外 server + Tavily 远程端点自动注册。"""
        servers = dict(self.mcp_servers)
        key = self.tavily_api_key.strip()
        if key:
            servers.setdefault("tavily", {
                "transport": "streamable_http",
                "url": f"https://mcp.tavily.com/mcp/?tavilyApiKey={key}",
            })
        return servers
```

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/test_mcp_config.py -v`
Expected: PASS（7 个测试）

- [ ] **Step 6: 回归既有配置测试**

Run: `uv run pytest tests/ -q`
Expected: 全部通过（现有测试不受影响）

- [ ] **Step 7: Commit**

```bash
git add common/config.py tests/test_mcp_config.py pyproject.toml uv.lock
git commit -m "feat: BotConfig 新增 MCP 配置字段（mcp_enabled/mcp_servers/tavily_api_key）"
```

---

### Task 2: 工具统一层 build_tools（内部工具包装为 BaseTool）

**Files:**
- Create: `bot/core/tools/factory.py`
- Modify: `bot/core/tools/__init__.py`（**只新增** `build_tools` 导出，保留现有 `TOOL_SCHEMA*` 导出——Task 4 才删除）
- Test: `tests/test_tools_factory.py`

**Interfaces:**
- Consumes: `bot.core.tools.search_chat_history.search_chat_history(query, rag_service, thread_id, user_name="", hours=0, content_keyword="", start_time="", end_time="")`（纯函数，保持原签名）；`bot.core.tools.user_memory.remember_user_memory(key, value, memory_store, user_id)` / `recall_user_memory(keyword, memory_store, user_id)`；`StubRagService`（`enabled`、`search`、`search_by_user`、`raise_on_search`）与 `StubMemoryStore`（`store_memory`/`load_memories`）
- Produces: `build_tools(rag_service=None, memory_store=None, mcp_tools=None) -> list[BaseTool]`，工具名 `search_chat_history` / `remember_user_memory` / `recall_user_memory`；MCP 工具原样并入

- [ ] **Step 1: 写失败测试 `tests/test_tools_factory.py`**

```python
"""build_tools 工具统一层测试：组装、schema 排除注入参数。"""

from bot.core.tools import build_tools
from tests.fakes import StubMemoryStore, StubRagService


def _names(tools):
    return {t.name for t in tools}


def test_rag_tool_present_when_rag_enabled():
    tools = build_tools(rag_service=StubRagService(), memory_store=StubMemoryStore())
    assert "search_chat_history" in _names(tools)


def test_no_rag_tool_when_disabled():
    tools = build_tools(rag_service=StubRagService(enabled=False), memory_store=None)
    assert "search_chat_history" not in _names(tools)


def test_memory_tools_present_when_store_injected():
    tools = build_tools(rag_service=None, memory_store=StubMemoryStore())
    assert {"remember_user_memory", "recall_user_memory"} <= _names(tools)


def test_mcp_tools_appended():
    class FakeMcpTool:
        name = "web_search"

    tools = build_tools(rag_service=None, memory_store=None, mcp_tools=[FakeMcpTool()])
    assert "web_search" in _names(tools)


def test_llm_schema_excludes_injected_args():
    tools = build_tools(rag_service=StubRagService(), memory_store=StubMemoryStore())
    by_name = {t.name: t for t in tools}

    search = by_name["search_chat_history"]
    props = search.tool_call_schema.model_json_schema()["properties"]
    assert "thread_id" not in props
    assert {"query", "user_name", "content_keyword", "start_time", "end_time", "hours"} <= set(props)

    recall = by_name["recall_user_memory"]
    recall_props = recall.tool_call_schema.model_json_schema()["properties"]
    assert "user_id" not in recall_props
    assert "keyword" in recall_props
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_tools_factory.py -v`
Expected: FAIL —— `ImportError`（`bot.core.tools.build_tools` 不存在）

- [ ] **Step 3: 实现 `bot/core/tools/factory.py`**

```python
"""工具统一层：把内部纯函数 + MCP 工具归一为 BaseTool 列表。

- 内部工具（RAG 检索、用户记忆）用 StructuredTool.from_function 包装：
  服务依赖经闭包绑定，thread_id/user_id 经 InjectedState 从图 state 注入，
  异常降级为占位文案「工具执行失败。」。
- MCP 工具（外部服务）已是 BaseTool，直接并入。

InjectedState 是 InjectedToolArg 子类：LangChain 的 tool_call_schema 自动
排除注入参数（LLM 看不到），ToolNode 执行时从 graph state 注入。
"""

import logging
from typing import Annotated

from langchain_core.tools import BaseTool, StructuredTool
from langgraph.prebuilt import InjectedState

from bot.core.tools.search_chat_history import search_chat_history
from bot.core.tools.user_memory import recall_user_memory, remember_user_memory

logger = logging.getLogger(__name__)

SEARCH_TOOL_DESCRIPTION = (
    "检索群聊历史消息。双模式："
    "（1）语义检索——当用户询问之前讨论过的话题、事实、决定、约定时用 query 检索最相关内容；"
    "（2）按人/按内容/按时间属性检索——当用户问『某人说过什么』『谁说过xx』『bot 回复过谁』"
    "或『最近一段时间内』时，用 user_name / content_keyword / start_time / end_time / hours"
    "精确过滤（更快更准）。"
)

REMEMBER_TOOL_DESCRIPTION = (
    "保存当前用户的持久性个人信息（名字、偏好、习惯、背景等）。"
    "当用户提到新的持久事实时调用；更新已有记忆时直接以相同 key 覆盖。"
)

RECALL_TOOL_DESCRIPTION = (
    "检索当前用户的持久记忆（名字、偏好、习惯、背景等）。"
    "当需要用户的个人信息、或回想之前提到过的用户事实时使用。"
    "keyword 留空返回全部记忆，否则按 key/value 模糊匹配。"
)


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
            return await search_chat_history(
                query, rag_service, thread_id, user_name, hours,
                content_keyword, start_time, end_time,
            )
        except Exception:
            logger.exception("search_chat_history failed")
            return "工具执行失败。"

    # 注意：async 函数必须走 coroutine= 参数（@tool 装饰器同款路由），
    # StructuredTool.from_function(func=<async>) 不会自动把异步函数挂到 coroutine，
    # 那样同步 _run() 会返回协程对象而非执行。
    return StructuredTool.from_function(
        coroutine=_run,
        name="search_chat_history",
        description=SEARCH_TOOL_DESCRIPTION,
    )


def _make_memory_tools(memory_store) -> list[BaseTool]:
    async def _remember(
        key: str,
        value: str,
        user_id: Annotated[str, InjectedState("user_id")] = "",
    ) -> str:
        try:
            return await remember_user_memory(key, value, memory_store, user_id)
        except Exception:
            logger.exception("remember_user_memory failed")
            return "工具执行失败。"

    async def _recall(
        keyword: str = "",
        user_id: Annotated[str, InjectedState("user_id")] = "",
    ) -> str:
        try:
            return await recall_user_memory(keyword, memory_store, user_id)
        except Exception:
            logger.exception("recall_user_memory failed")
            return "工具执行失败。"

    return [
        StructuredTool.from_function(
            coroutine=_remember, name="remember_user_memory", description=REMEMBER_TOOL_DESCRIPTION,
        ),
        StructuredTool.from_function(
            coroutine=_recall, name="recall_user_memory", description=RECALL_TOOL_DESCRIPTION,
        ),
    ]


def build_tools(rag_service=None, memory_store=None, mcp_tools=None) -> list[BaseTool]:
    """组装当前可用工具列表（BaseTool）。

    - rag_service 存在且启用 → search_chat_history
    - memory_store 存在 → remember/recall_user_memory
    - mcp_tools（BaseTool 列表）→ 直接并入
    """
    tools: list[BaseTool] = []
    if rag_service is not None and rag_service.enabled:
        tools.append(_make_search_tool(rag_service))
    if memory_store is not None:
        tools += _make_memory_tools(memory_store)
    tools += list(mcp_tools or [])
    return tools
```

- [ ] **Step 4: 导出 `build_tools`**

修改 `bot/core/tools/__init__.py`，**保留现有 TOOL_SCHEMA 相关导出**，仅新增 `build_tools`：

```python
from .factory import build_tools
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
    "build_tools",  # 新增
]
```

（Task 4 会删除 `TOOL_SCHEMA*` 相关导出，届时此文件再收敛。）

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/test_tools_factory.py -v`
Expected: PASS（5 个测试，schema 排除注入参数验证通过）

- [ ] **Step 6: 回归**

Run: `uv run pytest tests/ -q`
Expected: 全部通过（此步是纯增量，现有工具/测试不受影响）

- [ ] **Step 7: Commit**

```bash
git add bot/core/tools/factory.py bot/core/tools/__init__.py tests/test_tools_factory.py
git commit -m "feat: build_tools 工具统一层，内部工具包装为 BaseTool（InjectedState 注入 + 异常降级）"
```

---

### Task 3: call_llm + graph 迁移到 ToolNode（原子变更，一个任务）

> 为什么合并：`call_llm_node` 签名从 `(rag_service=, memory_store=)` 改为 `(tools=, use_memory=)` 后，旧 `graph.py` 传参会在调用时 `TypeError`。call_llm 与 graph 接线必须同一步完成，否则中间状态全库测试红。

**Files:**
- Modify: `bot/core/nodes/llm_node/call_llm.py`（整个文件重写）
- Modify: `bot/core/graph.py`
- Modify: `bot/core/nodes/__init__.py`（移除 `tool_node` 导出）
- Delete: `bot/core/nodes/tool_node/__init__.py`、`bot/core/nodes/tool_node/tool_node.py`（整个目录）
- Rewrite: `tests/test_tool_node.py`
- Modify: `tests/test_call_llm_node.py`（整个文件重写）
- Modify: `tests/test_memory_store_tool_integration.py`
- Verify: `tests/test_graph.py`（不应改动，跑通即回归）

**Interfaces:**
- Consumes: `build_tools(...) -> list[BaseTool]`（Task 2）
- Produces: `call_llm_node(state, llm, tools=None, use_memory=False, bot_config=None) -> dict`；`create_graph(llm, config, db_dir="db", rag_service=None, memory_store=None, vision_service=None, mcp_tools=None)`；图中节点名 `tools` = `ToolNode(tools)`；条件边 `call_llm → tools | summarize`

- [ ] **Step 1: 重写 `tests/test_call_llm_node.py`（失败测试）**

```python
import asyncio

from langchain_core.messages import AIMessage, HumanMessage

from bot.core.nodes import call_llm_node
from bot.core.tools import build_tools
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


def _full_tools():
    return build_tools(rag_service=StubRagService(), memory_store=StubMemoryStore())


def _memory_tools():
    return build_tools(rag_service=None, memory_store=StubMemoryStore())


def test_returns_tool_calls_when_tools_bound():
    llm = ScriptedLLM([AIMessage(content="", tool_calls=TOOL_CALLS)])
    state = BASE | {"tool_rounds": 0}
    result = asyncio.run(call_llm_node(
        state, llm=llm, tools=_full_tools(), use_memory=True, bot_config=CONFIG_ON,
    ))
    assert result["reply_text"] == ""
    assert result["tool_rounds"] == 1
    assert result["messages"][0].tool_calls


def test_returns_tool_calls_when_only_memory_tools():
    llm = ScriptedLLM([AIMessage(content="", tool_calls=MEMORY_CALLS)])
    state = BASE | {"tool_rounds": 0}
    result = asyncio.run(call_llm_node(
        state, llm=llm, tools=_memory_tools(), use_memory=True, bot_config=CONFIG_ON,
    ))
    assert result["messages"][0].tool_calls
    assert result["tool_rounds"] == 1


def test_returns_final_reply_when_no_tool_calls():
    llm = ScriptedLLM([AIMessage(content="最终回复")])
    state = BASE | {"tool_rounds": 0}
    result = asyncio.run(call_llm_node(
        state, llm=llm, tools=_full_tools(), use_memory=True, bot_config=CONFIG_ON,
    ))
    assert result["reply_text"] == "最终回复"
    assert "tool_rounds" not in result
    assert not result["messages"][0].tool_calls


def test_plain_path_when_no_tools():
    llm = ScriptedLLM([AIMessage(content="普通")])
    result = asyncio.run(call_llm_node(BASE, llm=llm, tools=None, bot_config=CONFIG_ON))
    assert result["reply_text"] == "普通"


def test_plain_path_when_rounds_exhausted():
    llm = ScriptedLLM([AIMessage(content="耗尽后收尾")])
    state = BASE | {"tool_rounds": 1}
    config = BotConfig(rag_enabled=True, rag_max_agent_rounds=1)
    result = asyncio.run(call_llm_node(
        state, llm=llm, tools=_full_tools(), use_memory=True, bot_config=config,
    ))
    assert result["reply_text"] == "耗尽后收尾"
    assert not result["messages"][0].tool_calls


def test_memory_hint_injected_when_use_memory():
    llm = ScriptedLLM([AIMessage(content="好")])
    state = BASE | {"tool_rounds": 0}
    asyncio.run(call_llm_node(
        state, llm=llm, tools=_memory_tools(), use_memory=True, bot_config=CONFIG_ON,
    ))
    assert any(
        "recall_user_memory" in getattr(m, "content", "")
        for m in llm.last_messages
    )
```

- [ ] **Step 2: 重写 `tests/test_tool_node.py`（驱动 ToolNode）**

（保留原测试矩阵，改为驱动 `ToolNode(build_tools(...))`。已核实 langgraph 1.2.2：未知工具返回 `status="error"` 的 ToolMessage 且 content 含工具名；无 tool_calls 时返回 `{"messages": []}`；工具异常经内部包装层降级为 `"工具执行失败。"`。）

```python
import asyncio

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.prebuilt import ToolNode

from bot.core.tools import build_tools
from tests.fakes import StubMemoryStore, StubRagService

RAG_CALL = AIMessage(content="", tool_calls=[
    {"name": "search_chat_history", "args": {"query": "之前聊了什么"},
     "id": "call_1", "type": "tool_call"},
])
RAG_CALL_USER = AIMessage(content="", tool_calls=[
    {"name": "search_chat_history",
     "args": {"query": "", "user_name": "张三", "hours": 24},
     "id": "call_5", "type": "tool_call"},
])
TIME_CALL = AIMessage(content="", tool_calls=[
    {"name": "search_chat_history",
     "args": {"query": "", "start_time": "2026-07-01", "end_time": "2026-08-01T23:59:59"},
     "id": "call_6", "type": "tool_call"},
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
    {"thread_id": "test:thread", "sender_id": "u1", "sender_name": "张三",
     "receiver_id": "bot1", "receiver_name": "小助手",
     "content": "之前聊了 RAG", "timestamp": "2026-07-30 10:00:00",
     "score": 0.8},
]

DEFAULT_STATE = {"thread_id": "test:thread", "user_id": "u1"}


def _node(*, rag=None, store=None):
    return ToolNode(build_tools(rag_service=rag, memory_store=store))


def test_executes_search_chat_history_query_mode():
    rag = StubRagService(search_results=SAMPLE)
    result = asyncio.run(_node(rag=rag, store=StubMemoryStore()).ainvoke(
        {"messages": [RAG_CALL], **DEFAULT_STATE}))
    assert isinstance(result["messages"][0], ToolMessage)
    assert "之前聊了 RAG" in result["messages"][0].content
    assert rag.last_query == "之前聊了什么"
    assert rag.last_thread_id == "test:thread"


def test_executes_search_by_user_sql_mode():
    rag = StubRagService(search_results=SAMPLE)
    result = asyncio.run(_node(rag=rag, store=StubMemoryStore()).ainvoke(
        {"messages": [RAG_CALL_USER], **DEFAULT_STATE}))
    assert "之前聊了 RAG" in result["messages"][0].content
    assert rag.last_person == "张三"
    assert rag.last_thread_id == "test:thread"


def test_executes_search_by_time_window():
    rag = StubRagService(search_results=SAMPLE)
    result = asyncio.run(_node(rag=rag, store=StubMemoryStore()).ainvoke(
        {"messages": [TIME_CALL], **DEFAULT_STATE}))
    assert "之前聊了 RAG" in result["messages"][0].content
    assert rag.last_start_time == "2026-07-01 00:00:00"
    assert rag.last_end_time == "2026-08-01 23:59:59"


def test_executes_recall_to_memory():
    store = StubMemoryStore()
    store.store_memory("u1", "喜欢的食物", "火锅")
    result = asyncio.run(_node(store=store).ainvoke(
        {"messages": [RECALL_CALL], **DEFAULT_STATE}))
    assert "火锅" in result["messages"][0].content


def test_executes_remember_to_memory():
    store = StubMemoryStore()
    result = asyncio.run(_node(store=store).ainvoke(
        {"messages": [REMEMBER_CALL], **DEFAULT_STATE}))
    assert "已记住" in result["messages"][0].content
    assert store.load_memories("u1") == [{"key": "喜欢的食物", "value": "火锅"}]


def test_unknown_tool_returns_error_message():
    result = asyncio.run(_node(rag=StubRagService(), store=StubMemoryStore()).ainvoke(
        {"messages": [UNKNOWN_CALL], **DEFAULT_STATE}))
    msg = result["messages"][0]
    assert isinstance(msg, ToolMessage)
    assert msg.status == "error"
    assert "no_such_tool" in msg.content


def test_noop_without_tool_calls():
    state = {"messages": [AIMessage(content="普通回复")], **DEFAULT_STATE}
    result = asyncio.run(_node(rag=StubRagService(), store=StubMemoryStore()).ainvoke(state))
    assert result["messages"] == []


def test_degrades_on_tool_error():
    rag = StubRagService(raise_on_search=True)
    result = asyncio.run(_node(rag=rag).ainvoke(
        {"messages": [RAG_CALL], **DEFAULT_STATE}))
    assert result["messages"][0].content == "工具执行失败。"
```

- [ ] **Step 3: 更新 `tests/test_memory_store_tool_integration.py`**

（替换 import 与调用，docstring 同步改 ToolNode。）最终文件：

```python
"""集成测试：真实 MemoryStore 经 ToolNode 执行记忆工具。

使用真实 SQLite 数据库（tmp_path），覆盖 asyncio.to_thread 线程池调用路径。
若 MemoryStore 未做线程安全处理（check_same_thread=True + 无锁），每次读写
都会在 to_thread 线程抛 sqlite3.ProgrammingError，被工具包装层降级为
「工具执行失败。」—— 本测试用于兜住这一类回归。
"""

import asyncio

from langchain_core.messages import AIMessage
from langgraph.prebuilt import ToolNode

from bot.core.memory import MemoryStore
from bot.core.tools import build_tools
from tests.fakes import make_state

REMEMBER_CALL = AIMessage(content="", tool_calls=[
    {"name": "remember_user_memory", "args": {"key": "名字", "value": "张三"},
     "id": "call_r", "type": "tool_call"},
])
RECALL_CALL = AIMessage(content="", tool_calls=[
    {"name": "recall_user_memory", "args": {"keyword": "名字"},
     "id": "call_c", "type": "tool_call"},
])


def _node(store):
    return ToolNode(build_tools(memory_store=store))


def test_memory_tools_work_with_real_store(tmp_path):
    store = MemoryStore(db_dir=str(tmp_path))

    # 写入：真实 MemoryStore 经 ToolNode 执行 remember（to_thread 线程池路径）
    remember_state = make_state(messages=[REMEMBER_CALL], user_id="u1")
    remember_result = asyncio.run(_node(store).ainvoke(remember_state))
    remember_content = remember_result["messages"][0].content
    assert "已记住" in remember_content
    assert "工具执行失败。" not in remember_content

    # 检索：真实 MemoryStore 经 ToolNode 执行 recall，应命中刚写入的记忆
    recall_state = make_state(messages=[RECALL_CALL], user_id="u1")
    recall_result = asyncio.run(_node(store).ainvoke(recall_state))
    recall_content = recall_result["messages"][0].content
    assert "张三" in recall_content
    assert "工具执行失败。" not in recall_content
```

（`make_state` 已含 `thread_id`/`user_id` 等 state 键，ToolNode 据此注入 InjectedState 参数。）

- [ ] **Step 4: 运行测试确认失败**

Run: `uv run pytest tests/test_call_llm_node.py -v`
Expected: FAIL —— `TypeError`（`call_llm_node` 不接受 `tools=`/`use_memory=` 关键字）。`tests/test_tool_node.py` 与 `tests/test_memory_store_tool_integration.py` 此刻可能已过（它们只依赖 Task 2 的 `build_tools`+`ToolNode`），属预期。

- [ ] **Step 5: 改 `bot/core/nodes/__init__.py`**

```python
from .action_node import describe_image_node, detect_intent, index_turn_node, summarize_node
from .llm_node import call_llm_node, router_node

__all__ = [
    "call_llm_node", "describe_image_node", "detect_intent", "index_turn_node",
    "router_node", "summarize_node",
]
```

- [ ] **Step 6: 重写 `bot/core/nodes/llm_node/call_llm.py`**

```python
import logging

from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI

from bot.core.utils import build_system_messages
from common import BotConfig, MEMORY_TOOL_HINT
from object.bot.state import BotState

logger = logging.getLogger(__name__)


async def call_llm_node(
    state: BotState,
    llm: ChatOpenAI,
    tools: list[BaseTool] | None = None,
    use_memory: bool = False,
    bot_config: BotConfig | None = None,
) -> dict:
    """调用 LLM 生成回复。

    SystemMessages 每次调用动态构建、不持久化，人设始终位于 messages[0]。
    tools 非空时绑定工具：若 LLM 请求调用工具，返回原始 AIMessage（带
    tool_calls），由 ToolNode 执行并回环重入本节点；否则返回最终回复。
    轮次达到 rag_max_agent_rounds 上限后走无工具路径收尾。
    """
    persona = state["persona"].format(bot_name=state.get("bot_name", ""))
    summary = state.get("conversation_summary", "").strip()
    system_msgs = build_system_messages(persona, summary)
    if use_memory:
        system_msgs.append(SystemMessage(content=MEMORY_TOOL_HINT))

    messages = system_msgs + state["messages"]

    tools = tools or []
    max_rounds = bot_config.rag_max_agent_rounds if bot_config is not None else 3
    rounds = state.get("tool_rounds", 0)

    if tools and rounds < max_rounds:
        try:
            response = await llm.bind_tools(tools).ainvoke(messages)
        except Exception as exc:
            _log_llm_error(exc, state.get("thread_id", ""))
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
        _log_llm_error(exc, state.get("thread_id", ""))
        return "我暂时无法思考，请稍后再试"


def _log_llm_error(exc: Exception, thread_id: str) -> None:
    if isinstance(exc, TimeoutError) or "Timeout" in type(exc).__name__:
        logger.warning("LLM call timed out for thread %s", thread_id)
    else:
        logger.exception("LLM call failed for thread %s", thread_id)
```

- [ ] **Step 7: 改 `bot/core/graph.py`**

头部 import 变更（删 `tool_node` import，加 `ToolNode` 与 `build_tools`）：

```python
from langgraph.prebuilt import ToolNode

from bot.core.nodes import (
    call_llm_node,
    describe_image_node,
    detect_intent,
    index_turn_node,
    summarize_node,
)
from bot.core.tools import build_tools
from bot.core.utils.routing import route_after_detect
from common import BotConfig
from object.bot.state import BotState
```

在 `_route_after_detect` 后新增路由函数：

```python
def _route_after_llm(state: BotState) -> str:
    """call_llm 后路由：末条消息带 tool_calls → tools（ToolNode），否则 → summarize。"""
    last = state["messages"][-1]
    return "tools" if getattr(last, "tool_calls", None) else "summarize"
```

改 `create_graph` 签名与组装逻辑：

```python
async def create_graph(
    llm: ChatOpenAI,
    config: BotConfig,
    db_dir: str = "db",
    rag_service=None,
    memory_store=None,
    vision_service=None,
    mcp_tools=None,
) -> tuple[CompiledStateGraph, AsyncSqliteSaver]:
    """Build and compile the conversation graph.

    Returns ``(graph, checkpointer)`` so the caller can manage the
    checkpointer's lifecycle.
    """
    tools = build_tools(
        rag_service=rag_service, memory_store=memory_store, mcp_tools=mcp_tools,
    )
    use_memory = memory_store is not None

    builder = StateGraph(BotState)
    builder.add_node("detect_intent", detect_intent)
    builder.add_node(
        "call_llm", partial(
            call_llm_node,
            llm=llm,
            tools=tools,
            use_memory=use_memory,
            bot_config=config,
        )
    )
    builder.add_node("summarize", partial(summarize_node, llm=llm, bot_config=config))
    builder.add_node("index_turn", partial(index_turn_node, rag_service=rag_service))
    builder.add_node("describe_image", partial(describe_image_node, vision_service=vision_service))
    builder.add_node("tools", ToolNode(tools))

    builder.add_edge(START, "detect_intent")
    builder.add_conditional_edges("detect_intent", _route_after_detect)
    builder.add_conditional_edges("call_llm", _route_after_llm)
    builder.add_edge("tools", "call_llm")
    builder.add_edge("describe_image", "call_llm")
    builder.add_edge("summarize", "index_turn")
    builder.add_edge("index_turn", END)

    checkpoint_path = os.path.join(db_dir, "checkpoint.sqlite")
    conn = await aiosqlite.connect(checkpoint_path)
    checkpointer = AsyncSqliteSaver(conn)
    graph = builder.compile(checkpointer=checkpointer)
    logger.info("LangGraph compiled with AsyncSqliteSaver checkpointing (db=%s)", checkpoint_path)
    return graph, checkpointer
```

- [ ] **Step 8: 删除 `bot/core/nodes/tool_node/` 目录**

Run: `git rm -r bot/core/nodes/tool_node/`

- [ ] **Step 9: 运行相关测试确认通过**

Run: `uv run pytest tests/test_call_llm_node.py tests/test_tool_node.py tests/test_memory_store_tool_integration.py tests/test_graph.py -v`
Expected: PASS（`test_graph.py` 用 `ScriptedLLM.bind_tools`（忽略工具列表），既有 graph 断言无需改动）

- [ ] **Step 10: 全量回归**

Run: `uv run pytest tests/ -q`
Expected: 全部通过。

- [ ] **Step 11: Commit**

```bash
git add bot/core/nodes/llm_node/call_llm.py bot/core/graph.py bot/core/nodes/__init__.py tests/test_call_llm_node.py tests/test_tool_node.py tests/test_memory_store_tool_integration.py
git rm -r bot/core/nodes/tool_node/
git commit -m "refactor: call_llm + graph 迁移到 prebuilt ToolNode，移除自定义 tool_node 手工分发"
```

---

### Task 4: 删除手写 TOOL_SCHEMA 裸 dict（schema 收敛到签名推断）

**Files:**
- Modify: `bot/core/tools/search_chat_history.py`（删 `TOOL_NAME`/`TOOL_DESCRIPTION`/`TOOL_SCHEMA`）
- Modify: `bot/core/tools/user_memory.py`（删 `TOOL_NAME_REMEMBER`/`TOOL_NAME_RECALL`/`TOOL_SCHEMA_REMEMBER`/`TOOL_SCHEMA_RECALL`）
- Modify: `bot/core/tools/__init__.py`（收敛导出）
- Modify: `tests/test_search_chat_history.py`（删 schema 断言测试）
- Modify: `tests/test_user_memory.py`（删 schema 断言测试）

**Interfaces:**
- Consumes: `build_tools` 中的 `SEARCH_TOOL_DESCRIPTION` 等描述常量（Task 2 已定义）
- Produces: `bot.core.tools` 只导出 `build_tools` / `search_chat_history` / `recall_user_memory` / `remember_user_memory`

- [ ] **Step 1: 改 `tests/test_search_chat_history.py`**

删第 3 行 import 中的 `TOOL_SCHEMA`（改为 `from bot.core.tools import search_chat_history`），并删整个 `test_tool_schema_exposes_query_param` 测试函数（schema 覆盖已由 `tests/test_tools_factory.py::test_llm_schema_excludes_injected_args` 承担）。

- [ ] **Step 2: 改 `tests/test_user_memory.py`**

删 import 中的 `TOOL_SCHEMA_RECALL`/`TOOL_SCHEMA_REMEMBER`（改为 `from bot.core.tools import recall_user_memory, remember_user_memory`），并删整个 `test_schemas_expose_expected_params` 测试函数。

- [ ] **Step 3: 删 `bot/core/tools/search_chat_history.py` 中的 schema 常量**

删除第 13 行起的 `TOOL_NAME = "search_chat_history"`、`TOOL_DESCRIPTION = (...)`、`TOOL_SCHEMA = {...}` 三个定义（描述文案已迁入 factory 的 `SEARCH_TOOL_DESCRIPTION`）。保留 `_format_time`/`_format_results`/`search_chat_history`。同时更新文件顶部 docstring：

```python
"""search_chat_history 工具（纯函数）。

纯函数：按查询检索群聊历史并格式化为上下文文本块。
rag_service 与 thread_id 由 factory 包装层在调用时注入，LLM 无需知道内部标识。
"""
```

- [ ] **Step 4: 删 `bot/core/tools/user_memory.py` 中的 schema 常量**

删除第 9 行起的 `TOOL_NAME_REMEMBER`/`TOOL_NAME_RECALL`/`TOOL_SCHEMA_REMEMBER`/`TOOL_SCHEMA_RECALL` 定义。保留 `_format_memories`/`remember_user_memory`/`recall_user_memory`。更新文件顶部 docstring：

```python
"""用户记忆工具（纯函数）：remember_user_memory / recall_user_memory。

保存/检索当前用户的持久记忆并格式化为文本。
memory_store 与 user_id 由 factory 包装层在调用时注入，LLM 无需知道内部标识。
"""
```

- [ ] **Step 5: 收敛 `bot/core/tools/__init__.py`**

```python
from .factory import build_tools
from .search_chat_history import search_chat_history
from .user_memory import recall_user_memory, remember_user_memory

__all__ = [
    "build_tools",
    "search_chat_history",
    "recall_user_memory", "remember_user_memory",
]
```

- [ ] **Step 6: 运行测试确认通过**

Run: `uv run pytest tests/test_search_chat_history.py tests/test_user_memory.py tests/test_tools_factory.py -v`
Expected: PASS

- [ ] **Step 7: 全量回归 + 全仓确认无残留引用**

Run: `uv run pytest tests/ -q`
Expected: 全部通过。

Run: `grep -rn "TOOL_SCHEMA\|TOOL_NAME" bot/ tests/ || echo "no residual TOOL_SCHEMA refs"`
Expected: 无输出（或 `no residual ...`）。

- [ ] **Step 8: Commit**

```bash
git add bot/core/tools/ tests/test_search_chat_history.py tests/test_user_memory.py
git commit -m "refactor: 删除手写 TOOL_SCHEMA 裸 dict，schema 统一由签名 + description 推断"
```

---

### Task 5: MCP 加载模块 + 本地 server 端到端测试

**Files:**
- Create: `bot/core/mcp/__init__.py`
- Create: `bot/core/mcp/client.py`
- Test: `tests/test_mcp_local_server.py`

**Interfaces:**
- Consumes: `MultiServerMCPClient`（来自 `langchain_mcp_adapters.client`）
- Produces: `load_mcp_tools(servers: dict, *, tool_name_prefix: bool = False) -> list[BaseTool]`（逐 server 加载，单 server 失败降级跳过，不阻断）

- [ ] **Step 1: 写失败测试 `tests/test_mcp_local_server.py`**

（本地 `FastMCP` stdio 子进程，**不打外网**。`mcp` 包自带 `mcp.server.fastmcp.FastMCP`，已随 Task 1 依赖引入。）

```python
"""MCP 加载端到端测试：本地 FastMCP stdio server（不打外网）。

验证 load_mcp_tools → ToolNode 执行 → ToolMessage 内容正确，
以及单个 server 加载失败降级跳过、不阻断。
"""

import asyncio
import sys

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.prebuilt import ToolNode

from bot.core.mcp import load_mcp_tools

SERVER_CODE = '''
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("math")

@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two numbers"""
    return a + b

if __name__ == "__main__":
    mcp.run(transport="stdio")
'''


def _stdio_servers(server_file) -> dict:
    return {
        "math": {
            "transport": "stdio",
            "command": sys.executable,
            "args": [str(server_file)],
        },
    }


def test_mcp_tools_load_and_execute(tmp_path):
    server = tmp_path / "math_server.py"
    server.write_text(SERVER_CODE, encoding="utf-8")

    async def scenario():
        tools = await asyncio.wait_for(
            load_mcp_tools(_stdio_servers(server)), timeout=30)
        assert {t.name for t in tools} == {"add"}

        node = ToolNode(tools)
        call = AIMessage(content="", tool_calls=[
            {"name": "add", "args": {"a": 3, "b": 5}, "id": "call_add", "type": "tool_call"},
        ])
        result = await asyncio.wait_for(
            node.ainvoke({"messages": [call]}), timeout=30)
        assert isinstance(result["messages"][0], ToolMessage)
        assert "8" in result["messages"][0].content

    asyncio.run(scenario())


def test_mcp_server_load_failure_skips():
    servers = {
        "broken": {
            "transport": "stdio",
            "command": sys.executable,
            "args": ["-c", "import sys; sys.exit(1)"],
        },
    }

    async def scenario():
        tools = await asyncio.wait_for(load_mcp_tools(servers), timeout=30)
        assert tools == []

    asyncio.run(scenario())
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_mcp_local_server.py -v`
Expected: FAIL —— `ModuleNotFoundError: bot.core.mcp`（模块尚未创建）

- [ ] **Step 3: 实现 `bot/core/mcp/client.py`**

```python
"""MCP 外部工具加载：连接多 MCP server，归一为 BaseTool 列表。

使用 langchain-mcp-adapters 的 MultiServerMCPClient。每个 MCP 工具调用时
自建 session（streamable_http=新 HTTP 会话），无需长期持有连接。
单个 server 加载失败降级跳过，不阻断 bot 启动。
"""

import logging

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

logger = logging.getLogger(__name__)


async def load_mcp_tools(servers: dict, *, tool_name_prefix: bool = False) -> list[BaseTool]:
    """从配置的 MCP servers 加载全部工具；单 server 失败跳过。

    Args:
        servers: ``{server_name: Connection}`` 连接配置字典（transport 可为
            streamable_http / stdio / sse / websocket）。
        tool_name_prefix: 为 True 时工具名加 ``<server>_`` 前缀，防多 server
            工具名冲突。

    Returns:
        LangChain BaseTool 列表；任一 server 加载失败被跳过（记日志）。
    """
    if not servers:
        return []
    client = MultiServerMCPClient(servers, tool_name_prefix=tool_name_prefix)
    tools: list[BaseTool] = []
    for name in client.connections:
        try:
            tools += await client.get_tools(server_name=name)
        except Exception:
            logger.exception("MCP server %s 加载失败，跳过", name)
    return tools
```

- [ ] **Step 4: 实现 `bot/core/mcp/__init__.py`**

```python
from bot.core.mcp.client import load_mcp_tools

__all__ = ["load_mcp_tools"]
```

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/test_mcp_local_server.py -v`
Expected: PASS（2 个测试）。⚠️ 若 stdio 子进程在 Windows 上偶发超时/失败，先单独重跑确认是否 flaky；若稳定失败，检查 `mcp` SDK 的 stdio 在 Windows ProactorEventLoop 下的行为并记录，不要盲目改断言。

- [ ] **Step 6: Commit**

```bash
git add bot/core/mcp/ tests/test_mcp_local_server.py
git commit -m "feat: MCP 加载模块（load_mcp_tools 逐 server 降级加载）+ 本地 server 端到端测试"
```

---

### Task 6: main.py 接线 + .env-template

**Files:**
- Modify: `main.py`
- Modify: `.env-template`

**Interfaces:**
- Consumes: `load_mcp_tools`（Task 5）、`BotConfig.mcp_enabled` / `.mcp_server_connections()`（Task 1）、`create_graph(..., mcp_tools=...)`（Task 3）

- [ ] **Step 1: 改 `main.py`**

在 `from bot import (...)` import 块后追加：

```python
from bot.core.mcp import load_mcp_tools
```

在 `memory_store = MemoryStore(db_dir=config.db_dir)` 之后、`graph, checkpointer = await create_graph(...)` 之前插入：

```python
    mcp_tools = []
    if config.mcp_enabled:
        mcp_tools = await load_mcp_tools(config.mcp_server_connections())
        logger.info("Loaded %d MCP tools", len(mcp_tools))
```

把 `create_graph` 调用改为传入 `mcp_tools=mcp_tools`：

```python
    graph, checkpointer = await create_graph(
        llm, config, db_dir=config.db_dir, rag_service=rag_service, memory_store=memory_store,
        vision_service=vision_service, mcp_tools=mcp_tools,
    )
```

- [ ] **Step 2: 改 `.env-template`**

在已有的 `TAVILY_API_KEY = <your-tavily-apikey>` 行后追加：

```
# --- MCP 外部工具（可选） ---
# BOT_MCP_ENABLED = 1
# BOT_MCP_SERVERS = {}
# BOT_MCP_TOOL_NAME_PREFIX = 0
```

- [ ] **Step 3: 验证 import 与编译**

Run: `uv run python -c "import main; print('main OK')"`
Expected: `main OK`（不联网；若本机 `.env` 未设 `BOT_MCP_ENABLED`，不会触发 `load_mcp_tools`）

- [ ] **Step 4: 全量回归**

Run: `uv run pytest tests/ -q`
Expected: 全部通过。

- [ ] **Step 5: Commit**

```bash
git add main.py .env-template
git commit -m "feat: main.py 接线 MCP 工具加载（mcp_enabled 门控，失败降级不阻断启动）"
```

---

### Task 7: CLAUDE.md 同步

**Files:**
- Modify: `CLAUDE.md`

**Interfaces:**
- 无（文档）。按下方给出的各处「改后」文案编辑。

- [ ] **Step 1: 架构树（Architecture 段）**

`bot/core/` 块：在 `tools/` 条目后补 `mcp/`；`nodes/` 块删除 `tool_node/` 行；`tools/` 注释改为纯函数 + factory。

改后相关行：

```
bot/
  core/
    mcp/                       #  MCP 外部工具加载（langchain-mcp-adapters，远程/stdio 多 server）
      client.py                #   load_mcp_tools — 逐 server 降级加载，返回 BaseTool 列表
    nodes/
      action_node/             #   detect_intent (routing), summarize (context window management), index_turn (RAG 入库)
      llm_node/                #   call_llm — invoke LLM（router 保留但未接线）
      subgraph/                #   nested subgraphs (future)
    tools/                     # Tool definitions imported by graph / tools node
      factory.py               #   build_tools — 内部纯函数包装为 BaseTool（InjectedState 注入 + 异常降级）
      search_chat_history.py   #   search_chat_history 纯函数（无 TOOL_SCHEMA，schema 由签名推断）
      user_memory.py           #   remember/recall_user_memory 纯函数（无 TOOL_SCHEMA）
```

- [ ] **Step 2: 数据流（Data flow 段）**

把 `tool_node (tool_node)` 一项替换为：

```
    → call_llm (llm_node)          ← dynamic SystemMessage injection
      tools (ToolNode)         ← call_llm 返回 tool_calls 时由 prebuilt ToolNode 统一执行（RAG/记忆/MCP 工具），回环到 call_llm
```

- [ ] **Step 3: 节点依赖注入（Key patterns 段）**

把 `tool_node` bound 一项替换为：

```
- `tools` node = `ToolNode(build_tools(rag_service=rag_service, memory_store=memory_store, mcp_tools=mcp_tools))` — 内部工具闭包绑定服务 + `InjectedState` 注入 thread_id/user_id，MCP 工具直接并入
```

- [ ] **Step 4: `create_graph()` 签名段**

把 `create_graph(llm, config, db_dir="db", rag_service=None, memory_store=None)` 改为 `create_graph(llm, config, db_dir="db", rag_service=None, memory_store=None, vision_service=None, mcp_tools=None)`，并补一句：`mcp_tools` 为 `BaseTool` 列表，由 `bot/core/mcp/client.py::load_mcp_tools` 加载后传入 `call_llm`（绑定）与 `ToolNode`（执行）。

- [ ] **Step 5: RAG 段 + 记忆工具段**

RAG 段 `tool_node` 字样改为 `tools（ToolNode）`，并补「内部工具经 `build_tools` 包装为 BaseTool、schema 由签名推断」。记忆工具段同理：`tool_node` → `tools（ToolNode）`，删除「按工具名分发」描述，改为「由 prebuilt ToolNode 统一执行；`user_id` 经 `InjectedState("user_id")` 注入」。

- [ ] **Step 6: Node type convention 段**

`tool_node/` 条目替换为：

```
- **`tools` node** — prebuilt `langgraph.prebuilt.ToolNode`，统一执行全部工具（RAG 检索、用户记忆、MCP 外部工具），经条件边回环；工具列表由 `build_tools` 组装
- **`bot/core/mcp/`** — MCP 外部工具加载（`load_mcp_tools`），远程 streamable_http / stdio 多 server，单 server 失败降级跳过
```

- [ ] **Step 7: Gotchas 工具定位 段**

把「工具定位」条目改为：

- **工具定位**: RAG 工具纯函数在 `bot/core/tools/search_chat_history.py`，记忆工具纯函数在 `bot/core/tools/user_memory.py`；`build_tools`（`bot/core/tools/factory.py`）把它们包装为 `BaseTool`（服务闭包绑定、`InjectedState` 注入 `thread_id`/`user_id`、异常降级为「工具执行失败。」）；执行节点为 prebuilt `ToolNode`。MCP 外部工具由 `bot/core/mcp/client.py::load_mcp_tools` 加载（每次调用自建 session，`handle_tool_errors=True` 把执行错误转 `status="error"` ToolMessage）。`MemoryStore` 表仍为 `db/memory.sqlite`。工具调用消息会持久化到 checkpoint（不同于 SystemMessage）。

- [ ] **Step 8: env 清单**

RAG 配置段后追加：

```
- **MCP**（env `BOT_MCP_ENABLED` / `BOT_MCP_SERVERS` / `BOT_MCP_TOOL_NAME_PREFIX` / `TAVILY_API_KEY`；Tavily 走官方远程 streamable_http 端点 `https://mcp.tavily.com/mcp/?tavilyApiKey=...`）
```

- [ ] **Step 9: 校验与提交**

Run: `uv run pytest tests/ -q`（确认无回归）
Expected: 全部通过。

```bash
git add CLAUDE.md
git commit -m "docs: CLAUDE.md 同步 ToolNode 迁移与 MCP 接入"
```

---

## 完成定义

- [ ] `uv run pytest tests/ -q` 全绿
- [ ] 全仓无 `TOOL_SCHEMA` / 自定义 `tool_node`（节点）残留引用
- [ ] `main.py` 启动时 `mcp_enabled=0` 不联网、bot 正常启动；`mcp_enabled=1` + `TAVILY_API_KEY` 时加载 Tavily 工具（`tavily-search`/`tavily-extract`/`tavily-map`/`tavily-crawl`），失败降级为无 web 工具照常启动
- [ ] CLAUDE.md 与实现一致
