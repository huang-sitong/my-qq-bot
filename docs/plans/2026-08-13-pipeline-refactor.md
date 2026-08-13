# 消息流水线裁剪与后台 RAG 索引 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 RAG 索引和上下文压缩移出 LangGraph，回复路径最小化，并让 handler 通过领域事件、Router、ContextCompactor 和 IndexWorker 完成解耦。

**Architecture:** Satori 协议事件先归一化为 `IncomingMessage`；消息 worker 用 Router 决定 command/reply/context_only/ignore；压缩在 reply/context_only 处理前同步执行；最小 graph 只保留图片预处理、LLM 和工具回环；RAG 索引通过独立 `IndexWorker` 异步消费。

**Tech Stack:** Python 3.12、LangGraph、asyncio、SQLite checkpoint、Milvus-lite、Ollama embeddings。

**Spec:** `docs/designs/2026-08-13-pipeline-refactor-design.md`

## Global Constraints

- 使用 `uv sync` 安装依赖，`uv run ruff check` 检查 lint，`uv run python -m pytest` 跑测试。
- Python >= 3.12。
- `BotState.active_skills` 绝不从 handler 输入注入，节点只能 `state.get("active_skills", [])`。
- checkpoint 继续使用本地 SQLite，不引入分布式存储。
- RAG 是可重建缓存，后台索引失败只记日志，不阻塞回复。
- 不新增 Kafka、重试、熔断、DLQ 或事件存储。
- 新增 `object/` 类型时同步更新 `object/__init__.py` 与 `object/bot/__init__.py` 的懒加载映射。

---

### Task 1: 领域事件数据对象

**Files:**
- Create: `object/bot/message.py`
- Create: `object/bot/index_task.py`
- Modify: `object/bot/__init__.py`
- Modify: `object/__init__.py`
- Test: `tests/test_domain_message.py`

**Interfaces:**
- Consumes: 无。
- Produces:
  - `IncomingMessage`：handler 入队对象。
  - `IndexTurnTask`：IndexWorker 消费对象。

- [ ] **Step 1: Write the failing test**

```python
from object import IncomingMessage, IndexTurnTask


def test_incoming_message_is_immutable_domain_event():
    msg = IncomingMessage(
        event_id="llonebot:1:m1",
        platform="llonebot",
        guild_id="g1",
        thread_id="llonebot:g1:c1",
        channel_id="c1",
        channel_type=0,
        user_id="u1",
        user_name="张三",
        raw_content="<img src=\"https://x/1.jpg\"/>",
        content_kind="image",
        has_text=False,
        llm_text="[图片]",
        clean_text="",
        mentions={},
        image_srcs=["https://x/1.jpg"],
    )
    assert msg.thread_id == "llonebot:g1:c1"
    assert msg.image_srcs == ["https://x/1.jpg"]


def test_index_turn_task_is_immutable():
    task = IndexTurnTask(
        thread_id="t1",
        user_id="u1",
        user_name="张三",
        bot_id="bot1",
        bot_name="小助手",
        user_message="你好",
        bot_reply="收到",
    )
    assert task.bot_reply == "收到"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_domain_message.py -v`

Expected: FAIL with `ModuleNotFoundError` or import error.

- [ ] **Step 3: Create the data objects**

`object/bot/message.py`:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class IncomingMessage:
    event_id: str
    platform: str
    guild_id: str
    thread_id: str
    channel_id: str
    channel_type: int
    user_id: str
    user_name: str
    raw_content: str
    content_kind: str
    has_text: bool
    llm_text: str
    clean_text: str
    mentions: dict[str, str]
    image_srcs: list[str]
```

`object/bot/index_task.py`:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class IndexTurnTask:
    thread_id: str
    user_id: str
    user_name: str
    bot_id: str
    bot_name: str
    user_message: str
    bot_reply: str
```

`object/bot/__init__.py` 保留现有 `__getattr__`/`__dir__`，只替换 `__all__`
与 `_module_map`：

```python
__all__ = [
    "Attachment",
    "BotState",
    "IncomingMessage",
    "IndexTurnTask",
    "MessageKind",
    "ParsedContent",
]

_module_map = {
    "BotState": "state",
    "Attachment": "content",
    "IncomingMessage": "message",
    "IndexTurnTask": "index_task",
    "MessageKind": "content",
    "ParsedContent": "content",
}
```

`object/__init__.py` 的 `__all__` 与 `_BOT_NAMES` 各加两个名字：

```python
_BOT_NAMES = {
    "Attachment",
    "BotState",
    "IncomingMessage",
    "IndexTurnTask",
    "MessageKind",
    "ParsedContent",
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_domain_message.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add object/bot/message.py object/bot/index_task.py object/bot/__init__.py object/__init__.py tests/test_domain_message.py
git commit -m "feat: add pipeline domain events"
```

---

### Task 2: Router 纯决策

**Files:**
- Create: `bot/core/router.py`
- Test: `tests/test_router.py`

**Interfaces:**
- Consumes: `IncomingMessage` from Task 1；现有 `decide_reply`、`keep_in_context`、`parse_command`。
- Produces:
  - `RouteAction`：`COMMAND/REPLY/CONTEXT_ONLY/IGNORE`。
  - `RouteDecision`：包含 action、command、actor、parsed_command、should_respond、keep_in_context。
  - `route_incoming(...) -> RouteDecision`。

- [ ] **Step 1: Write the failing test**

`tests/test_router.py`:

```python
from bot.core.commands import Command, CommandRegistry
from bot.core.router import RouteAction, route_incoming
from object import IncomingMessage


async def _ping(ctx):
    return "Pong."


def _message(**overrides):
    data = dict(
        event_id="e1",
        platform="llonebot",
        guild_id="",
        thread_id="llonebot::c1",
        channel_id="c1",
        channel_type=1,
        user_id="u1",
        user_name="张三",
        raw_content="/ping",
        content_kind="text",
        has_text=True,
        llm_text="/ping",
        clean_text="/ping",
        mentions={},
        image_srcs=[],
    )
    data.update(overrides)
    return IncomingMessage(**data)


def _registry():
    registry = CommandRegistry()
    registry.register(Command(
        name="ping",
        description="ping",
        usage="/ping",
        permission="everyone",
        handler=_ping,
    ))
    return registry


def test_command_route():
    decision = route_incoming(
        _message(),
        command_registry=_registry(),
        command_enabled=True,
        command_prefix="/",
    )
    assert decision.action == RouteAction.COMMAND
    assert decision.command.name == "ping"
    assert decision.parsed_command.args == ()


def test_unknown_command_falls_through_to_reply_in_private():
    decision = route_incoming(
        _message(raw_content="/unknown", clean_text="/unknown", llm_text="/unknown"),
        command_registry=_registry(),
        command_enabled=True,
        command_prefix="/",
        bot_id="bot1",
        bot_name="小助手",
    )
    assert decision.action == RouteAction.REPLY


def test_group_non_mention_text_is_context_only():
    decision = route_incoming(
        _message(
            guild_id="g1",
            thread_id="llonebot:g1:c1",
            channel_id="c1",
            channel_type=0,
            raw_content="晚上吃什么",
            clean_text="晚上吃什么",
            llm_text="晚上吃什么",
        ),
        auto_reply_allowed=False,
    )
    assert decision.action == RouteAction.CONTEXT_ONLY
    assert decision.keep_in_context is True


def test_group_auto_reply_is_reply():
    decision = route_incoming(
        _message(
            guild_id="g1",
            thread_id="llonebot:g1:c1",
            channel_type=0,
            raw_content="晚上吃什么",
            clean_text="晚上吃什么",
            llm_text="晚上吃什么",
        ),
        auto_reply_allowed=True,
    )
    assert decision.action == RouteAction.REPLY


def test_pure_media_is_ignored():
    decision = route_incoming(
        _message(
            channel_type=0,
            raw_content="<img src=\"https://x/1.jpg\"/>",
            content_kind="image",
            has_text=False,
            llm_text="[图片]",
            clean_text="",
            image_srcs=["https://x/1.jpg"],
        ),
        auto_reply_allowed=False,
    )
    assert decision.action == RouteAction.IGNORE
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_router.py -v`

Expected: FAIL with `ModuleNotFoundError: bot.core.router`

- [ ] **Step 3: Implement Router**

`bot/core/router.py`:

```python
from dataclasses import dataclass
from enum import Enum

from bot.core.commands import (
    Command,
    CommandActor,
    CommandRegistry,
    ParsedCommand,
    parse_command,
)
from bot.core.utils.routing import decide_reply, keep_in_context
from object.bot.message import IncomingMessage


class RouteAction(str, Enum):
    COMMAND = "command"
    REPLY = "reply"
    CONTEXT_ONLY = "context_only"
    IGNORE = "ignore"


@dataclass(frozen=True)
class RouteDecision:
    action: RouteAction
    command: Command | None = None
    actor: CommandActor | None = None
    parsed_command: ParsedCommand | None = None
    should_respond: bool = False
    keep_in_context: bool = False


def route_incoming(
    message: IncomingMessage,
    *,
    command_registry: CommandRegistry | None = None,
    command_enabled: bool = False,
    command_prefix: str = "/",
    bot_id: str = "",
    bot_name: str = "",
    auto_reply_allowed: bool = False,
    admin_ids: tuple[str, ...] = (),
) -> RouteDecision:
    if (
        command_enabled
        and command_registry is not None
        and message.content_kind == "text"
    ):
        parsed_command = parse_command(message.clean_text, command_prefix)
        if parsed_command is not None:
            command = command_registry.resolve(parsed_command.name)
            if command is not None:
                actor = CommandActor(
                    user_id=message.user_id,
                    name=message.user_name,
                    is_admin=message.user_id in admin_ids,
                )
                return RouteDecision(
                    action=RouteAction.COMMAND,
                    command=command,
                    actor=actor,
                    parsed_command=parsed_command,
                )

    should_respond = decide_reply(
        message.channel_type,
        message.content_kind,
        bot_id,
        bot_name,
        message.mentions,
        auto_reply_allowed,
    )
    keep = keep_in_context(
        should_respond,
        message.content_kind,
        message.has_text,
    )
    if not keep:
        return RouteDecision(action=RouteAction.IGNORE)
    if should_respond:
        return RouteDecision(
            action=RouteAction.REPLY,
            should_respond=True,
            keep_in_context=True,
        )
    return RouteDecision(
        action=RouteAction.CONTEXT_ONLY,
        should_respond=False,
        keep_in_context=True,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_router.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bot/core/router.py tests/test_router.py
git commit -m "feat: add incoming message router"
```

---

### Task 3: ContextCompactor

**Files:**
- Create: `bot/core/compaction.py`
- Test: `tests/test_compaction.py`

**Interfaces:**
- Consumes: `estimate_context_tokens`、`summarize_node`。
- Produces:
  - `ContextCompactor(graph, llm, config, skill_registry=None)`
  - `async compact_if_needed(thread_id: str) -> int`
  - `async force_compact(thread_id: str) -> int`

- [ ] **Step 1: Write the failing test**

`tests/test_compaction.py`:

```python
import asyncio
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage

from bot.core.compaction import ContextCompactor
from common import BotConfig
from tests.fakes import ScriptedLLM


class _FakeGraph:
    def __init__(self, values):
        self.values = values
        self.updates = []

    async def aget_state(self, config):
        return SimpleNamespace(values=self.values)

    async def aupdate_state(self, config, updates):
        self.updates.append(updates)


def _state(messages):
    return {
        "messages": messages,
        "persona": "你是{bot_name}",
        "conversation_summary": "",
        "active_skills": [],
    }


def test_compact_if_needed_noop_below_threshold():
    llm = ScriptedLLM([])
    graph = _FakeGraph(_state([HumanMessage(content="hi")]))
    config = BotConfig(_env_file=None, llm_context_window=10_000)
    compactor = ContextCompactor(graph, llm, config)

    removed = asyncio.run(compactor.compact_if_needed("t1"))

    assert removed == 0
    assert graph.updates == []


def test_compact_if_needed_writes_summary_above_threshold():
    llm = ScriptedLLM([AIMessage(content="压缩后的摘要")])
    graph = _FakeGraph(_state([
        HumanMessage(content="x" * 2000),
        AIMessage(content="y" * 2000),
    ]))
    config = BotConfig(
        _env_file=None,
        llm_context_window=100,
        summary_trigger_ratio=0.5,
        summary_keep_ratio=0.01,
    )
    compactor = ContextCompactor(graph, llm, config)

    removed = asyncio.run(compactor.compact_if_needed("t1"))

    assert removed > 0
    assert graph.updates
    assert graph.updates[0]["conversation_summary"] == "压缩后的摘要"


def test_force_compact_noop_when_nothing_removable():
    llm = ScriptedLLM([])
    graph = _FakeGraph(_state([]))
    config = BotConfig(_env_file=None)
    compactor = ContextCompactor(graph, llm, config)

    removed = asyncio.run(compactor.force_compact("t1"))

    assert removed == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_compaction.py -v`

Expected: FAIL with `ModuleNotFoundError: bot.core.compaction`

- [ ] **Step 3: Implement ContextCompactor**

`bot/core/compaction.py`:

```python
import logging

from bot.core.nodes import summarize_node
from bot.core.utils import estimate_context_tokens

logger = logging.getLogger(__name__)


class ContextCompactor:
    def __init__(self, graph, llm, config, skill_registry=None):
        self._graph = graph
        self._llm = llm
        self._config = config
        self._skill_registry = skill_registry

    async def compact_if_needed(self, thread_id: str) -> int:
        thread_config = {"configurable": {"thread_id": thread_id}}
        snapshot = await self._graph.aget_state(thread_config)
        if snapshot is None:
            return 0
        state = snapshot.values
        if not state.get("messages"):
            return 0
        total = estimate_context_tokens(
            state["messages"],
            state.get("persona", ""),
            state.get("conversation_summary", ""),
            skill_registry=self._skill_registry,
            active_skills=state.get("active_skills", []),
        )
        trigger = int(self._config.summary_trigger_ratio * self._config.llm_context_window)
        if total <= trigger:
            return 0
        return await self._compact_state(state, thread_config)

    async def force_compact(self, thread_id: str) -> int:
        thread_config = {"configurable": {"thread_id": thread_id}}
        snapshot = await self._graph.aget_state(thread_config)
        if snapshot is None:
            return 0
        return await self._compact_state(snapshot.values, thread_config)

    async def _compact_state(self, state: dict, thread_config: dict) -> int:
        try:
            result = await summarize_node(
                state,
                llm=self._llm,
                bot_config=self._config,
                skill_registry=self._skill_registry,
                force=True,
            )
        except Exception:
            logger.exception("Context compaction failed for thread %s", state.get("thread_id", ""))
            return 0
        if not result:
            return 0
        await self._graph.aupdate_state(thread_config, result)
        return len(result.get("messages", []))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_compaction.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bot/core/compaction.py tests/test_compaction.py
git commit -m "feat: add context compactor"
```

---

### Task 4: IndexWorker

**Files:**
- Create: `bot/core/rag/index_worker.py`
- Test: `tests/test_index_worker.py`

**Interfaces:**
- Consumes: `IndexTurnTask` from Task 1、`RagService.index_turn(...)`。
- Produces:
  - `IndexWorker(rag_service, maxsize=1000)`
  - `async start()`
  - `async enqueue(task) -> bool`
  - `async stop()`

- [ ] **Step 1: Write the failing test**

`tests/test_index_worker.py`:

```python
import asyncio

from bot.core.rag.index_worker import IndexWorker
from object import IndexTurnTask
from tests.fakes import StubRagService


def _task(text="你好", reply="收到"):
    return IndexTurnTask(
        thread_id="t1",
        user_id="u1",
        user_name="张三",
        bot_id="bot1",
        bot_name="小助手",
        user_message=text,
        bot_reply=reply,
    )


def test_index_worker_drains_enqueued_task():
    async def run():
        rag = StubRagService()
        worker = IndexWorker(rag)
        await worker.start()
        assert await worker.enqueue(_task())
        await worker.stop()
        assert rag.last_indexed == {
            "thread_id": "t1",
            "user_id": "u1",
            "user_name": "张三",
            "bot_id": "bot1",
            "bot_name": "小助手",
            "user_message": "你好",
            "bot_reply": "收到",
        }

    asyncio.run(run())


def test_index_worker_swallows_index_failure():
    class _RaisingRag:
        async def index_turn(self, **kwargs):
            raise RuntimeError("boom")

    async def run():
        worker = IndexWorker(_RaisingRag())
        await worker.start()
        assert await worker.enqueue(_task())
        await worker.stop()

    asyncio.run(run())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_index_worker.py -v`

Expected: FAIL with `ModuleNotFoundError: bot.core.rag.index_worker`

- [ ] **Step 3: Implement IndexWorker**

`bot/core/rag/index_worker.py`:

```python
import asyncio
import logging

from object.bot.index_task import IndexTurnTask

logger = logging.getLogger(__name__)


class IndexWorker:
    def __init__(self, rag_service, maxsize: int = 1000):
        self._rag_service = rag_service
        self._queue: asyncio.Queue[IndexTurnTask | None] = asyncio.Queue(maxsize=maxsize)
        self._task: asyncio.Task | None = None
        self._stopped = False

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def enqueue(self, task: IndexTurnTask) -> bool:
        if self._stopped:
            return False
        try:
            self._queue.put_nowait(task)
            return True
        except asyncio.QueueFull:
            logger.warning("RAG index queue full; dropping task for thread %s", task.thread_id)
            return False

    async def _run(self) -> None:
        while True:
            task = await self._queue.get()
            try:
                if task is None:
                    return
                await self._rag_service.index_turn(
                    thread_id=task.thread_id,
                    user_id=task.user_id,
                    user_name=task.user_name,
                    bot_id=task.bot_id,
                    bot_name=task.bot_name,
                    user_message=task.user_message,
                    bot_reply=task.bot_reply,
                )
            except Exception:
                logger.exception("RAG index task failed for thread %s", task.thread_id)
            finally:
                self._queue.task_done()

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stopped = True
        await self._queue.put(None)
        await self._task
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_index_worker.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bot/core/rag/index_worker.py tests/test_index_worker.py
git commit -m "feat: add background rag index worker"
```

---

### Task 5: 最小 Graph

**Files:**
- Modify: `bot/core/graph.py`
- Modify: `tests/test_graph.py`

**Interfaces:**
- Consumes: 既有 `describe_image_node`、`call_llm_node`、`skill_manager_node`、ToolNode。
- Produces: graph 只消费 handler 注入的 `messages`，不再注册 detect/summarize/index 节点。

- [ ] **Step 1: Update graph implementation**

`bot/core/graph.py` 的 import 改为：

```python
from bot.core.nodes import (
    call_llm_node,
    describe_image_node,
    skill_manager_node,
)
```

删除 `_route_after_detect` 函数，并把 `_route_after_llm` 改为：

```python
def _route_after_llm(state: BotState) -> str:
    last = state["messages"][-1]
    return "tools" if getattr(last, "tool_calls", None) else END
```

`create_graph` 中删除以下节点注册和边：

```python
builder.add_node("detect_intent", detect_intent)
builder.add_node("summarize", ...)
builder.add_node("index_turn", ...)
builder.add_edge(START, "detect_intent")
builder.add_conditional_edges("detect_intent", _route_after_detect)
builder.add_edge("summarize", "index_turn")
builder.add_edge("index_turn", END)
```

保留/新增如下 graph 组装：

```python
builder.add_node("describe_image", partial(
    describe_image_node,
    vision_service=vision_service,
    llm_multimodal=config.llm_multimodal,
    max_images=config.vision_max_images,
    timeout=config.vision_timeout,
))
builder.add_node("call_llm", partial(
    call_llm_node,
    llm=llm,
    tools=tools,
    use_memory=use_memory,
    use_mcp=use_mcp,
    use_bash=use_bash,
    use_file_send=file_sender is not None,
    bot_config=config,
    skill_registry=skill_registry,
))
builder.add_node("skill_manager", partial(skill_manager_node, skill_registry=skill_registry))
builder.add_node("tools", ToolNode(tools, handle_tool_errors=_tool_error_message))

builder.add_edge(START, "describe_image")
builder.add_edge("describe_image", "call_llm")
builder.add_conditional_edges("call_llm", _route_after_llm)
builder.add_edge("tools", "skill_manager")
builder.add_edge("skill_manager", "call_llm")
```

- [ ] **Step 2: Update graph tests for reply-only graph**

`tests/test_graph.py` 的 `_initial_state()` 增加输入消息：

```python
def _initial_state() -> dict:
    return {
        "messages": [HumanMessage(content="还记得我们聊过 RAG 吗？")],
        "thread_id": "test:thread",
        "channel_id": "private:u1",
        "persona": "你是{bot_name}",
        "reply_text": "",
        "should_respond": True,
        "bot_name": "测试机器人",
        "bot_id": "bot1",
        "channel_type": 1,
        "user_name": "张三",
        "user_id": "u1",
        "tool_rounds": 0,
        "content_kind": "text",
        "llm_text": "还记得我们聊过 RAG 吗？",
        "clean_text": "还记得我们聊过 RAG 吗？",
    }
```

删除以下三个非回复 graph 测试：

```text
test_group_non_mention_text_indexes_without_reply
test_group_non_mention_image_ends_without_index
test_group_non_mention_image_text_indexes_without_reply
```

新增两个测试：

```python
def test_graph_does_not_index_turn(tmp_path):
    rag = StubRagService()
    llm = ScriptedLLM([AIMessage(content="收到")])
    graph, _ = asyncio.run(
        create_graph(llm, BotConfig(rag_enabled=True), db_dir=str(tmp_path), rag_service=rag)
    )
    result = asyncio.run(graph.ainvoke(_initial_state(), {"configurable": {"thread_id": "test:thread"}}))
    assert result["reply_text"] == "收到"
    assert rag.last_indexed is None


def test_graph_input_message_appends_to_checkpoint(tmp_path):
    async def run():
        llm = ScriptedLLM([AIMessage(content="a"), AIMessage(content="b")])
        graph, checkpointer = await create_graph(
            llm, BotConfig(_env_file=None), db_dir=str(tmp_path)
        )
        cfg = {"configurable": {"thread_id": "test:thread"}}
        await graph.ainvoke(_initial_state(), cfg)
        second = _initial_state()
        second["messages"] = [HumanMessage(content="第二条")]
        await graph.ainvoke(second, cfg)
        snapshot = await graph.aget_state(cfg)
        assert len(snapshot.values["messages"]) == 4

    asyncio.run(run())
```

图片相关 graph 测试把 `_initial_state()` 的 `messages` 改为对应内容：

```python
state = {
    **_initial_state(),
    "content_kind": "image",
    "clean_text": "",
    "llm_text": "[图片]",
    "image_srcs": ["https://x/1.jpg"],
    "messages": [HumanMessage(content="[图片]")],
}
```

- [ ] **Step 3: Run graph tests**

Run: `uv run python -m pytest tests/test_graph.py -v`

Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add bot/core/graph.py tests/test_graph.py
git commit -m "refactor: shrink graph to reply pipeline"
```

---

### Task 6: MessageHandler 接入领域事件、Router、Compactor、IndexWorker

**Files:**
- Modify: `bot/handler.py`
- Modify: `tests/test_handler.py`
- Modify: `tests/test_message_worker_pool.py`
- Create: `tests/test_handler_pipeline.py`

**Interfaces:**
- Consumes: `IncomingMessage`、`RouteAction/route_incoming`、`ContextCompactor`、`IndexWorker`。
- Produces: handler 入队 `IncomingMessage`，回复后发送 reply 并生成 `IndexTurnTask`。

- [ ] **Step 1: Rewrite handler**

`bot/handler.py` 完整替换为：

```python
import asyncio
import logging
import random
import time

from langchain_core.messages import HumanMessage
from langgraph.graph.state import CompiledStateGraph as CompiledGraph

from bot.core.commands import (
    CommandActor,
    CommandContext,
    CommandRegistry,
    CommandServices,
    can_run,
    run_command,
)
from bot.core.compaction import ContextCompactor
from bot.core.rag.index_worker import IndexWorker
from bot.core.router import RouteAction, route_incoming
from bot.core.utils import IMAGE_PLACEHOLDER, MessageKind, content_to_text, parse_content
from bot.core.utils.reply_policy import should_allow_auto_reply
from bot.transport.http.client import SatoriApiClient
from bot.transport.websocket.client import SatoriClient
from object.bot.index_task import IndexTurnTask
from object.bot.message import IncomingMessage
from object.satori import ChannelType, EventBody, LoginList

logger = logging.getLogger(__name__)


class MessageHandler:
    def __init__(
        self,
        client: SatoriClient,
        graph: CompiledGraph,
        persona: str,
        api_client: SatoriApiClient,
        bot_config=None,
        command_registry: CommandRegistry | None = None,
        command_services: CommandServices | None = None,
        compactor: ContextCompactor | None = None,
        index_worker: IndexWorker | None = None,
        worker_count: int = 1,
        queue_maxsize: int = 0,
    ) -> None:
        self.client = client
        self.graph = graph
        self._persona = persona
        self._api_client = api_client
        self._bot_config = bot_config
        self._command_registry = command_registry
        self._command_services = command_services
        self._compactor = compactor
        self._index_worker = index_worker
        self._bot_id: str | None = None
        self._bot_name: str | None = None
        self._worker_count = worker_count
        self._queue: asyncio.Queue[IncomingMessage | None] = asyncio.Queue(maxsize=queue_maxsize)
        self._locks: dict[str, asyncio.Lock] = {}
        self._worker_tasks: list[asyncio.Task[None]] = []
        self._last_auto_reply_at: dict[str, float] = {}
        self._random = random.Random()

    async def start(self) -> None:
        self._worker_tasks = [
            asyncio.create_task(self._worker())
            for _ in range(self._worker_count)
        ]
        logger.info("Message workers started: %d", self._worker_count)

    async def stop(self) -> None:
        for _ in range(self._worker_count):
            await self._queue.put(None)
        if self._worker_tasks:
            await asyncio.gather(*self._worker_tasks, return_exceptions=True)
            self._worker_tasks = []
        logger.info("Message worker stopped")

    async def handle_login(self, login_list: LoginList) -> None:
        logins = login_list.logins
        if not logins:
            return
        user = logins[0].user
        if user is not None:
            self._bot_id = user.id
            self._bot_name = user.name or user.nick or user.id
            self._api_client.set_user_id(self._bot_id)
            if self._command_services is not None:
                self._command_services.bot_name = self._bot_name
            logger.info("Bot info set: id=%s name=%s", self._bot_id, self._bot_name)

    def _auto_reply_allowed(
        self,
        *,
        thread_id: str,
        channel_type: int,
        bot_id: str,
        bot_name: str,
        mentions: dict[str, str],
    ) -> bool:
        cfg = self._bot_config
        if cfg is None:
            return False
        last_reply = self._last_auto_reply_at.get(thread_id, 0.0)
        cooldown_elapsed = time.monotonic() - last_reply >= cfg.auto_reply_cooldown
        return should_allow_auto_reply(
            channel_type=channel_type,
            mentions=mentions,
            bot_id=bot_id,
            bot_name=bot_name,
            auto_reply_enabled=cfg.auto_reply,
            cooldown_elapsed=cooldown_elapsed,
            random_value=self._random.random(),
            rate=cfg.auto_reply_random_rate,
        )

    async def handle(self, event: EventBody) -> None:
        if event.message is None or event.message.content is None:
            return
        raw_content = event.message.content
        if not raw_content.strip():
            return

        platform = event.platform or "unknown"
        guild_id = event.guild.id if event.guild else ""
        channel_id = event.channel.id if event.channel else ""
        user_id = event.user.id if event.user else ""
        user_name = ""
        if event.user:
            user_name = event.user.nick or event.user.name or event.user.id or ""
        thread_id = f"{platform}:{guild_id}:{channel_id}"
        channel_type = int(event.channel.type) if event.channel else 0
        parsed = parse_content(raw_content)
        message = IncomingMessage(
            event_id=f"{platform}:{event.id}:{event.message.id}",
            platform=platform,
            guild_id=guild_id,
            thread_id=thread_id,
            channel_id=channel_id,
            channel_type=channel_type,
            user_id=user_id,
            user_name=user_name,
            raw_content=raw_content,
            content_kind=parsed.kind.value,
            has_text=parsed.has_text,
            llm_text=parsed.llm_text,
            clean_text=parsed.clean_text,
            mentions=parsed.mentions,
            image_srcs=[a.src for a in parsed.attachments if a.type == "img"],
        )
        await self._queue.put(message)

    async def _worker(self) -> None:
        while True:
            try:
                item = await self._queue.get()
                if item is None:
                    self._queue.task_done()
                    return
                lock = self._locks.setdefault(item.thread_id, asyncio.Lock())
                async with lock:
                    try:
                        await self._process(item)
                    except Exception:
                        logger.exception(
                            "Message processing failed for thread %s", item.thread_id
                        )
                self._queue.task_done()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Message worker loop error")

    async def _process(self, message: IncomingMessage) -> None:
        auto_reply_allowed = self._auto_reply_allowed(
            thread_id=message.thread_id,
            channel_type=message.channel_type,
            bot_id=self._bot_id or "",
            bot_name=self._bot_name or "",
            mentions=message.mentions,
        )
        decision = route_incoming(
            message,
            command_registry=self._command_registry,
            command_enabled=bool(
                self._bot_config is not None and self._bot_config.command_enabled
            ),
            command_prefix=self._bot_config.command_prefix if self._bot_config else "/",
            bot_id=self._bot_id or "",
            bot_name=self._bot_name or "",
            auto_reply_allowed=auto_reply_allowed,
            admin_ids=tuple(self._bot_config.admin_ids) if self._bot_config else (),
        )

        if decision.action == RouteAction.COMMAND:
            await self._execute_command(message, decision)
            return
        if decision.action == RouteAction.IGNORE:
            return
        if self.graph is None:
            return
        if self._compactor is not None:
            await self._compactor.compact_if_needed(message.thread_id)

        human = self._build_human_message(message)
        if decision.action == RouteAction.CONTEXT_ONLY:
            thread_config = {"configurable": {"thread_id": message.thread_id}}
            await self.graph.aupdate_state(thread_config, {"messages": [human]})
            await self._enqueue_index(message, "")
            return

        await self._run_reply_graph(message, human, auto_reply_allowed)

    async def _execute_command(self, message: IncomingMessage, decision) -> None:
        command = decision.command
        actor = decision.actor
        if command is None or actor is None:
            return
        if not can_run(command, actor):
            reply_text = "无权执行该指令。"
        elif decision.parsed_command is not None and decision.parsed_command.error:
            reply_text = f"指令参数错误，用法：{command.usage}"
        else:
            ctx = CommandContext(
                raw=message.raw_content,
                actor=actor,
                platform=message.platform,
                guild_id=message.guild_id,
                channel_id=message.channel_id,
                thread_id=message.thread_id,
                channel_type=message.channel_type,
                args=decision.parsed_command.args if decision.parsed_command else (),
                config=self._bot_config,
                services=self._command_services,
            )
            reply_text = (await run_command(command, ctx)).text
        logger.info(
            "Command /%s by %s (admin=%s, thread=%s)",
            command.name, message.user_id, actor.is_admin, message.thread_id,
        )
        if reply_text:
            await self._send_reply(message.channel_id, reply_text)

    def _build_human_message(self, message: IncomingMessage) -> HumanMessage:
        if message.channel_type != ChannelType.DIRECT and message.user_name:
            return HumanMessage(content=message.llm_text, name=message.user_name)
        return HumanMessage(content=message.llm_text)

    def _build_graph_input(
        self,
        message: IncomingMessage,
        human: HumanMessage,
        auto_reply_allowed: bool,
    ) -> dict:
        return {
            "thread_id": message.thread_id,
            "channel_id": message.channel_id,
            "persona": self._persona,
            "reply_text": "",
            "should_respond": True,
            "bot_name": self._bot_name or "",
            "bot_id": self._bot_id or "",
            "tool_rounds": 0,
            "user_id": message.user_id,
            "channel_type": message.channel_type,
            "user_name": message.user_name,
            "content_kind": message.content_kind,
            "has_text": message.has_text,
            "llm_text": message.llm_text,
            "clean_text": message.clean_text,
            "mentions": message.mentions,
            "image_srcs": message.image_srcs,
            "auto_reply": auto_reply_allowed,
            "messages": [human],
        }

    async def _run_reply_graph(
        self,
        message: IncomingMessage,
        human: HumanMessage,
        auto_reply_allowed: bool,
    ) -> None:
        max_rounds = (
            self._bot_config.rag_max_agent_rounds
            if self._bot_config is not None
            else 3
        )
        recursion_limit = 2 * max_rounds + 8
        try:
            result = await self.graph.ainvoke(
                self._build_graph_input(message, human, auto_reply_allowed),
                {
                    "configurable": {"thread_id": message.thread_id},
                    "recursion_limit": recursion_limit,
                },
            )
        except Exception:
            logger.exception("Graph invoke failed for thread %s", message.thread_id)
            return

        reply_text = result.get("reply_text", "")
        if reply_text:
            await self._send_reply(message.channel_id, reply_text)
        if reply_text and auto_reply_allowed:
            self._last_auto_reply_at[message.thread_id] = time.monotonic()
        await self._enqueue_index(message, reply_text)

    async def _enqueue_index(self, message: IncomingMessage, reply_text: str) -> None:
        if self._index_worker is None:
            return
        task = self._build_index_task(message, reply_text)
        if task is None:
            return
        await self._index_worker.enqueue(task)

    def _build_index_task(
        self, message: IncomingMessage, reply_text: str
    ) -> IndexTurnTask | None:
        user_message = message.clean_text
        reply_text = content_to_text(reply_text).strip()
        if (
            message.content_kind == MessageKind.IMAGE.value
            and (user_message.strip() or reply_text)
        ):
            user_message = f"{user_message} {IMAGE_PLACEHOLDER}".strip()
        if not user_message.strip() and not reply_text:
            return None
        return IndexTurnTask(
            thread_id=message.thread_id,
            user_id=message.user_id,
            user_name=message.user_name,
            bot_id=self._bot_id or "",
            bot_name=self._bot_name or "",
            user_message=user_message,
            bot_reply=reply_text,
        )

    async def _send_reply(self, channel_id: str, content: str) -> None:
        try:
            await self._api_client.send_message(channel_id, content)
        except Exception:
            logger.exception("Failed to send reply to channel %s", channel_id)
```

- [ ] **Step 2: Update existing handler tests**

`tests/test_handler.py` 和 `tests/test_message_worker_pool.py` 中所有直接调用
`handler._process({...})` 的测试改为构造相同 `EventBody` 后调用
`asyncio.run(handler.handle(event))`。`_make_handler` 增加可选参数：

```python
def _make_handler(
    graph,
    bot_config=None,
    command_registry=None,
    command_services=None,
    compactor=None,
    index_worker=None,
):
    return MessageHandler(
        client=object(),
        graph=graph,
        persona="你是{bot_name}",
        api_client=_StubApi(),
        bot_config=bot_config,
        command_registry=command_registry,
        command_services=command_services,
        compactor=compactor,
        index_worker=index_worker,
    )
```

保留原有断言不变；`test_channel_type_coerced_to_int_before_graph` 改为断言
`graph.state["channel_type"] is int`，并通过 `handle` 传入协议事件。

- [ ] **Step 3: Write handler pipeline integration tests**

`tests/test_handler_pipeline.py`：

```python
import asyncio

from bot.core.commands import CommandServices
from bot.core.rag.index_worker import IndexWorker
from bot.handler import MessageHandler
from common import BotConfig
from object.bot.index_task import IndexTurnTask
from object.satori import Channel, ChannelType, EventBody, Message, User
from tests.fakes import StubRagService


class _StubApi:
    def __init__(self):
        self.sent = []

    async def send_message(self, channel_id, content):
        self.sent.append((channel_id, content))


class _StubGraph:
    def __init__(self):
        self.state = None
        self.updates = []

    async def ainvoke(self, state, config):
        self.state = dict(state)
        return {"reply_text": "收到"}

    async def aget_state(self, config):
        return None

    async def aupdate_state(self, config, updates):
        self.updates.append(updates)


def _handler(graph, index_worker=None):
    return MessageHandler(
        client=object(),
        graph=graph,
        persona="你是{bot_name}",
        api_client=_StubApi(),
        bot_config=BotConfig(_env_file=None),
        command_services=CommandServices(version="test", started_at=0.0, bot_name=""),
        index_worker=index_worker,
    )


def _event(text, channel_type=ChannelType.DIRECT):
    return EventBody(
        id=1,
        sn=1,
        type="message-created",
        platform="llonebot",
        channel=Channel(id="c1", type=channel_type),
        user=User(id="u1", name="张三"),
        message=Message(id="m1", content=text),
    )


def test_reply_path_sends_reply_and_enqueues_index():
    async def run():
        rag = StubRagService()
        worker = IndexWorker(rag)
        await worker.start()
        graph = _StubGraph()
        handler = _handler(graph, index_worker=worker)
        await handler.handle(_event("你好"))
        await handler.stop()
        await worker.stop()
        assert handler._api_client.sent == [("c1", "收到")]
        assert rag.last_indexed is not None
        assert rag.last_indexed["bot_reply"] == "收到"

    asyncio.run(run())


def test_context_only_writes_checkpoint_and_indexes():
    async def run():
        rag = StubRagService()
        worker = IndexWorker(rag)
        await worker.start()
        graph = _StubGraph()
        handler = _handler(graph, index_worker=worker)
        event = _event(
            "群聊普通发言",
            channel_type=ChannelType.TEXT,
        )
        await handler.handle(event)
        await handler.stop()
        await worker.stop()
        assert graph.updates
        assert rag.last_indexed is not None
        assert rag.last_indexed["bot_reply"] == ""

    asyncio.run(run())


def test_ignore_path_writes_nothing():
    async def run():
        worker = IndexWorker(StubRagService())
        await worker.start()
        graph = _StubGraph()
        handler = _handler(graph, index_worker=worker)
        await handler.handle(_event(
            "<img src=\"https://x/1.jpg\"/>",
            channel_type=ChannelType.TEXT,
        ))
        await handler.stop()
        await worker.stop()
        assert graph.state is None
        assert graph.updates == []

    asyncio.run(run())
```

- [ ] **Step 4: Run handler tests**

Run: `uv run python -m pytest tests/test_handler.py tests/test_handler_pipeline.py tests/test_message_worker_pool.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bot/handler.py tests/test_handler.py tests/test_handler_pipeline.py tests/test_message_worker_pool.py
git commit -m "refactor: route messages outside graph and index in background"
```

---

### Task 7: main.py 装配与 /compact 复用

**Files:**
- Modify: `main.py`
- Modify: `bot/core/commands/model.py`
- Modify: `bot/core/commands/builtin.py`
- Modify: `tests/test_command_state_commands.py`

**Interfaces:**
- Consumes: `ContextCompactor`、`IndexWorker`、`CommandServices.compactor`。
- Produces: 运行期 `compactor` 与 `index_worker` 生命周期。

- [ ] **Step 1: Add compactor to CommandServices**

`bot/core/commands/model.py` 的 TYPE_CHECKING import 增加：

```python
from bot.core.compaction import ContextCompactor
```

字段增加：

```python
compactor: ContextCompactor | None = None
```

- [ ] **Step 2: Rewrite /compact**

`bot/core/commands/builtin.py` 的 `_compact`：

```python
async def _compact(ctx: CommandContext) -> CommandResult:
    compactor = ctx.services.compactor
    if compactor is None:
        return CommandResult(text="当前未启用上下文压缩。")
    removed = await compactor.force_compact(ctx.thread_id)
    if removed == 0:
        return CommandResult(text="当前上下文较少，无需压缩。")
    return CommandResult(text=f"已提前压缩上下文，移除 {removed} 条历史消息。")
```

- [ ] **Step 3: Update main.py**

`main.py` 增加 import：

```python
from bot.core.compaction import ContextCompactor
from bot.core.rag.index_worker import IndexWorker
```

在 `command_services` 构造前创建：

```python
compactor = None
if graph is not None and llm is not None:
    compactor = ContextCompactor(
        graph, llm, config, skill_registry=skill_registry,
    )
index_worker = None
if rag_service is not None:
    index_worker = IndexWorker(rag_service)
```

`CommandServices(...)` 增加 `compactor=compactor`。

`MessageHandler(...)` 增加 `compactor=compactor, index_worker=index_worker`。

启动顺序：

```python
await handler.start()
if index_worker is not None:
    await index_worker.start()
```

关闭顺序：

```python
finally:
    await handler.stop()
    if index_worker is not None:
        await index_worker.stop()
    await client.disconnect()
    await api_client.close()
    if rag_service is not None:
        rag_service.close()
```

- [ ] **Step 4: Update command tests**

`tests/test_command_state_commands.py` 的 `_ctx` 服务构造增加 `compactor` 或让
`/compact` 测试使用真实 `ContextCompactor(graph, llm, config, skill_registry)`；
断言从 “已提前压缩上下文” 开始即可，数字由 `force_compact` 返回。

- [ ] **Step 5: Run command and main-related tests**

Run: `uv run python -m pytest tests/test_command_state_commands.py tests/test_graph.py -v`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add main.py bot/core/commands/model.py bot/core/commands/builtin.py tests/test_command_state_commands.py
git commit -m "feat: wire compactor and index worker lifecycle"
```

---

### Task 8: 全量验证与清理

**Files:**
- Modify: 根据测试失败修正相关文件。

- [ ] **Step 1: Run ruff**

Run: `uv run ruff check`

Expected: PASS

- [ ] **Step 2: Run full test suite**

Run: `uv run python -m pytest`

Expected: PASS

- [ ] **Step 3: Fix remaining tests and rerun**

若旧测试仍直接调用 `_process(dict)` 或断言 graph 内索引，按 Task 6 的迁移规则修正，
然后重复 Step 1 和 Step 2。

- [ ] **Step 4: Commit final cleanup**

```bash
git add -A
git commit -m "chore: finalize pipeline refactor"
```

---

## Self-Review Notes

- Spec 的领域事件、Router、压缩、最小 graph、IndexWorker、生命周期、测试和改造顺序均落在 Task 1-8。
- `BotState.active_skills` 不在 `_build_graph_input` 中注入。
- `IndexTurnTask` 统一由 `object/bot/index_task.py` 导出。
- graph 不再注册 `summarize`/`index_turn`，压缩由 `ContextCompactor` 承担。
- 所有新接口在产生任务的 `Interfaces` 块中给出签名。
