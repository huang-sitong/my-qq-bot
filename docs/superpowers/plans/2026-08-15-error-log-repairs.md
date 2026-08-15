# 错误日志修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 2026-08-15 日志中 context-only 消息持续失败的外部 LangGraph 状态更新问题，并为 OneBot11 文件上传增加显式超时，同时补齐 jmcomic PDF 后处理依赖。

**Architecture:** 所有图外 `aupdate_state` 统一显式传 `as_node="describe_image"`，避免 LangGraph 在连续外部更新时无法推断写入节点；OneBot11 HTTP client 使用可配置超时；jmcomic skill 把 `img2pdf` 写进 requirements，避免重建 venv 后 PDF 后处理再次缺依赖。

**Tech Stack:** Python 3.12、LangGraph 1.2.2、pytest、httpx、pydantic-settings；不新增 bot 运行时依赖。

**Spec:** `.others/error_log.md`

## Global Constraints

- 验证命令：`uv sync`、`uv run pytest`、`uv run ruff check`。
- 现有测试必须全部保持通过，新增参数必须带默认值。
- 所有图外 `aupdate_state` 必须显式传 `as_node`，统一使用 `EXTERNAL_UPDATE_NODE = "describe_image"`。
- `BOT_ONEBOT11_TIMEOUT` 合法范围为 `>0`，默认 `60`。
- 不删除或迁移 `db/checkpoint.sqlite`；修复后新消息应继续从现有 checkpoint 后累积。
- Milvus `AllocTimestamp Method not implemented!` 是 milvus-lite 未实现 RPC + pymilvus `ignore_unimplemented` 的预期兼容行为，集合创建和检索正常，本计划不修改 Milvus 版本或日志级别。

## File Structure

- `src/bot/core/graph.py`：导出 `EXTERNAL_UPDATE_NODE`。
- `src/bot/core/dispatcher.py`：context-only 外部更新传 `as_node`。
- `src/bot/core/compaction.py`、`src/bot/core/commands/builtin.py`：同类外部更新传 `as_node`。
- `src/common/config.py`、`src/bot/transport/http/client.py`、`.env-template`：OneBot11 上传超时。
- `skills/jmcomic/requirements.txt`：PDF 后处理依赖。
- `AGENTS.md`：记录图外状态更新的不变量。

---

### Task 1: Dispatcher context-only 外部更新显式 `as_node`

**Files:**
- Modify: `src/bot/core/graph.py`
- Modify: `src/bot/core/dispatcher.py`
- Create: `tests/test_external_state_updates.py`
- Modify: `tests/test_handler_pipeline.py`

**Interfaces:**
- Produces: `bot.core.graph.EXTERNAL_UPDATE_NODE: str = "describe_image"`。
- Dispatcher 的单条/批量 context-only 更新调用 `aupdate_state(config, {"messages": ...}, as_node=EXTERNAL_UPDATE_NODE)`。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_external_state_updates.py`：

```python
"""图外状态更新：连续 context-only 批次必须都能写入真实 LangGraph checkpoint。"""

import asyncio

from bot.core.dispatcher import MessageDispatcher
from bot.core.graph import create_graph
from common import BotConfig
from domain.bot.identity import BotIdentity
from domain.bot.message import IncomingMessage
from domain.bot.router import RouteAction, RouteDecision
from tests.fakes import ScriptedLLM


class _NoopApi:
    async def send_message(self, channel_id: str, content: str) -> None:
        pass


def _message(thread_id: str, content: str) -> IncomingMessage:
    return IncomingMessage(
        event_id=f"e-{content}",
        platform="llonebot",
        guild_id="g",
        thread_id=thread_id,
        channel_id="c",
        channel_type=0,
        user_id="u1",
        user_name="u1",
        raw_content=content,
        content_kind="text",
        has_text=True,
        llm_text=content,
        clean_text=content,
        mentions={},
        image_srcs=[],
        trace_id=f"t-{content}",
    )


def test_context_only_batches_append_messages_across_calls(tmp_path):
    async def run():
        graph, checkpointer = await create_graph(
            ScriptedLLM([]),
            BotConfig(_env_file=None, rag_enabled=False),
            db_dir=str(tmp_path),
        )
        try:
            dispatcher = MessageDispatcher(
                graph=graph,
                persona="你是{bot_name}",
                api_client=_NoopApi(),
                bot_config=BotConfig(_env_file=None, rag_enabled=False),
                identity=BotIdentity(id="bot", name="bot"),
            )
            ctx = RouteDecision(action=RouteAction.CONTEXT_ONLY)
            await dispatcher.dispatch_batch(
                [_message("t1", "第一"), _message("t1", "第二")],
                [ctx, ctx],
            )
            await dispatcher.dispatch_batch(
                [_message("t1", "第三")],
                [ctx],
            )
            snapshot = await graph.aget_state(
                {"configurable": {"thread_id": "t1"}}
            )
            assert [m.content for m in snapshot.values["messages"]] == [
                "第一", "第二", "第三",
            ]
        finally:
            await checkpointer.conn.close()

    asyncio.run(run())
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_external_state_updates.py -v`

Expected: FAIL，第二次 `dispatch_batch` 抛 `langgraph.errors.InvalidUpdateError: Ambiguous update, specify as_node`。

- [ ] **Step 3: 实现常量与 Dispatcher 修复**

在 `src/bot/core/graph.py` 的 `logger = logging.getLogger(__name__)` 后追加：

```python
# 图外 aupdate_state 必须显式指定写入节点；describe_image 是消息进入图后的
# 第一个状态写入节点，连续外部更新时不会让 LangGraph 出现 Ambiguous update。
EXTERNAL_UPDATE_NODE = "describe_image"
```

在 `src/bot/core/dispatcher.py` 的 graph 相关导入附近追加：

```python
from bot.core.graph import EXTERNAL_UPDATE_NODE
```

把第 86 行附近改为：

```python
            await self.graph.aupdate_state(
                thread_config,
                {"messages": [human]},
                as_node=EXTERNAL_UPDATE_NODE,
            )
```

把第 134 行附近改为：

```python
        await self.graph.aupdate_state(
            thread_config,
            {"messages": humans},
            as_node=EXTERNAL_UPDATE_NODE,
        )
```

更新 `tests/test_handler_pipeline.py` 的 `_StubGraph`：

```python
class _StubGraph:
    def __init__(self):
        self.state = None
        self.updates = []
        self.last_as_node = None

    async def ainvoke(self, state, config):
        self.state = dict(state)
        return {"reply_text": "收到"}

    async def aget_state(self, config):
        return None

    async def aupdate_state(self, config, updates, as_node=None):
        self.last_as_node = as_node
        self.updates.append(updates)
```

在 `test_context_only_writes_checkpoint_and_indexes` 和 `test_batch_context_only_single_checkpoint_update` 中分别追加断言：

```python
        assert graph.last_as_node == "describe_image"
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_external_state_updates.py tests/test_handler_pipeline.py -v`

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add src/bot/core/graph.py src/bot/core/dispatcher.py tests/test_external_state_updates.py tests/test_handler_pipeline.py
git commit -m "fix: pass as_node for context-only graph updates"
```

---

### Task 2: compaction 与 `/clear` 外部更新同样显式 `as_node`

**Files:**
- Modify: `src/bot/core/compaction.py`
- Modify: `src/bot/core/commands/builtin.py`
- Modify: `tests/test_command_state_commands.py`
- Modify: `tests/test_compaction.py`

**Interfaces:**
- Consumes: `bot.core.graph.EXTERNAL_UPDATE_NODE`。
- `ContextCompactor._compact_state` 和 `_clear` 的 `aupdate_state` 都显式传该节点。

- [ ] **Step 1: 写失败测试**

在 `tests/test_command_state_commands.py` 顶部追加导入：

```python
from bot.core.graph import EXTERNAL_UPDATE_NODE
```

在文件末尾追加两个测试：

```python
def test_clear_works_after_external_context_updates(tmp_path):
    async def run():
        graph, checkpointer = await create_graph(
            ScriptedLLM([]),
            BotConfig(_env_file=None),
            db_dir=str(tmp_path),
        )
        try:
            cfg = {"configurable": {"thread_id": "t1"}}
            for text in ("旧一", "旧二"):
                await graph.aupdate_state(
                    cfg,
                    {"messages": [HumanMessage(content=text)]},
                    as_node=EXTERNAL_UPDATE_NODE,
                )
            services = CommandServices(
                version="test", started_at=0.0, bot_name="",
                graph=graph, checkpointer=checkpointer,
            )
            registry = build_command_registry(services)
            reply = await registry.resolve("clear").handler(_ctx(services))

            assert reply.text == "已清空当前会话上下文。"
            snapshot = await graph.aget_state(cfg)
            assert snapshot.values.get("messages", []) == []
        finally:
            await checkpointer.conn.close()

    asyncio.run(run())


def test_compact_works_after_external_context_updates(tmp_path):
    async def run():
        llm = ScriptedLLM([AIMessage(content="压缩后的摘要")])
        config = BotConfig(
            _env_file=None,
            llm_context_window=1000,
            summary_trigger_ratio=0.5,
            summary_keep_ratio=0.01,
        )
        graph, checkpointer = await create_graph(llm, config, db_dir=str(tmp_path))
        try:
            cfg = {"configurable": {"thread_id": "t1"}}
            for text in ("旧一", "旧二", "旧三"):
                await graph.aupdate_state(
                    cfg,
                    {"messages": [HumanMessage(content=text)]},
                    as_node=EXTERNAL_UPDATE_NODE,
                )
            compactor = ContextCompactor(graph, llm, config)
            removed = await compactor.force_compact("t1")

            assert removed > 0
            snapshot = await graph.aget_state(cfg)
            assert snapshot.values.get("conversation_summary") == "压缩后的摘要"
            assert len(snapshot.values.get("messages", [])) < 3
        finally:
            await checkpointer.conn.close()

    asyncio.run(run())
```

更新 `tests/test_compaction.py` 的 `_FakeGraph.aupdate_state`：

```python
    async def aupdate_state(self, config, updates, as_node=None):
        self.updates.append(updates)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_command_state_commands.py::test_clear_works_after_external_context_updates tests/test_command_state_commands.py::test_compact_works_after_external_context_updates -v`

Expected: FAIL，`aupdate_state` 抛 `InvalidUpdateError`。

- [ ] **Step 3: 实现修复**

在 `src/bot/core/compaction.py` 顶部追加：

```python
from bot.core.graph import EXTERNAL_UPDATE_NODE
```

把 `_compact_state` 内的写入改为：

```python
        await self._graph.aupdate_state(
            thread_config, result, as_node=EXTERNAL_UPDATE_NODE,
        )
```

在 `src/bot/core/commands/builtin.py` 顶部追加：

```python
from bot.core.graph import EXTERNAL_UPDATE_NODE
```

把 `_clear` 内的写入改为：

```python
    await graph.aupdate_state(
        config, updates, as_node=EXTERNAL_UPDATE_NODE,
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_command_state_commands.py tests/test_compaction.py -v`

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add src/bot/core/compaction.py src/bot/core/commands/builtin.py tests/test_command_state_commands.py tests/test_compaction.py
git commit -m "fix: pass as_node for compaction and clear state updates"
```

---

### Task 3: OneBot11 文件上传显式超时

**Files:**
- Modify: `src/common/config.py`
- Modify: `src/bot/transport/http/client.py`
- Modify: `.env-template`
- Modify: `tests/test_config.py`
- Modify: `tests/test_satori_api_client.py`

**Interfaces:**
- Produces: `BotConfig.onebot11_timeout: int = 60`，环境变量 `BOT_ONEBOT11_TIMEOUT`。
- `SatoriApiClient.onebot11_http` 使用 `httpx.Timeout(config.onebot11_timeout, connect=10)`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_config.py` 的 `EXPECTED_DEFAULTS` 中 `"onebot11_api_base_url"` 后追加：

```python
    "onebot11_timeout": 60,
```

在 `ENV_SAMPLES` 中 `"onebot11_api_base_url"` 后追加：

```python
    "onebot11_timeout": ("42", 42),
```

在文件末尾追加：

```python
def test_invalid_onebot11_timeout_rejected(monkeypatch):
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("BOT_ONEBOT11_TIMEOUT", "0")
    with pytest.raises(ValidationError):
        BotConfig(_env_file=None)
```

在 `tests/test_satori_api_client.py` 末尾追加：

```python
def test_onebot11_client_uses_configured_timeout():
    client = SatoriApiClient(BotConfig(
        _env_file=None,
        onebot11_api_base_url="http://onebot.test",
        onebot11_timeout=42,
    ))
    try:
        assert client.onebot11_http.timeout.read == 42
        assert client.onebot11_http.timeout.connect == 10
    finally:
        asyncio.run(client.close())
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_config.py tests/test_satori_api_client.py -v`

Expected: FAIL，字段不存在或 client 仍使用 httpx 默认 5 秒超时。

- [ ] **Step 3: 实现配置与 client**

在 `src/common/config.py` 的 `onebot11_api_base_url` 后追加：

```python
    onebot11_timeout: int = Field(
        default=60,
        gt=0,
        validation_alias="BOT_ONEBOT11_TIMEOUT",
    )
```

在 `.env-template` 的 Transport 段追加：

```text
# BOT_ONEBOT11_TIMEOUT = 60   # send_file 走 OneBot11 HTTP 的读取/总响应超时上限（秒）
```

把 `src/bot/transport/http/client.py` 的 `onebot11_http` 改为：

```python
    @property
    def onebot11_http(self) -> httpx.AsyncClient:
        if self._onebot11_http is None:
            self._onebot11_http = httpx.AsyncClient(
                base_url=self._config.onebot11_api_base_url,
                timeout=httpx.Timeout(
                    self._config.onebot11_timeout, connect=10,
                ),
            )
        return self._onebot11_http
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_config.py tests/test_satori_api_client.py -v`

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add src/common/config.py src/bot/transport/http/client.py .env-template tests/test_config.py tests/test_satori_api_client.py
git commit -m "feat: add configurable onebot11 upload timeout"
```

---

### Task 4: 本地 jmcomic PDF 后处理依赖修复（不提交）

**Files:**
- Modify (local only, ignored by `.gitignore`): `skills/jmcomic/requirements.txt`
- Modify (local only, ignored by `.gitignore`): `skills/jmcomic/SKILL.md`

**Interfaces:**
- 本机 `skills/jmcomic/requirements.txt` 包含 `img2pdf>=0.6.3`，重建 skill venv 后 `post_process.py --type pdf` 不再缺依赖。
- 仓库策略 `skills/*` 默认忽略（仅 `skills/soup` 例外），因此本任务只改本机文件，不创建测试、不提交。

- [ ] **Step 1: 修改本地依赖声明**

在 `skills/jmcomic/requirements.txt` 末尾追加：

```text
img2pdf>=0.6.3
```

- [ ] **Step 2: 更新本地 SKILL.md**

在 `skills/jmcomic/SKILL.md` 的 Runtime Environment 段安装命令后追加：

```text
`requirements.txt` 已包含 PDF 后处理所需 `img2pdf`；旧 venv 如缺少该依赖，重新执行上面的安装命令即可。
```

- [ ] **Step 3: 验证本机依赖**

Run: `skills/jmcomic/.venv/Scripts/python.exe -c "import img2pdf"`

Expected: 无异常。

- [ ] **Step 4: 确认不进入 git**

Run: `git status --short`

Expected: 不显示 `skills/jmcomic/*` 改动；该目录已被 `.gitignore` 的 `skills/*` 规则忽略。

---

### Task 5: 文档与全量验证

**Files:**
- Modify: `AGENTS.md`

**Interfaces:**
- No runtime interface. Documentation only。

- [ ] **Step 1: 更新 AGENTS.md**

在 `AGENTS.md` 的 Gotchas 段追加：

```markdown
- **图外 `aupdate_state`**：所有图外状态更新必须显式传 `as_node="describe_image"`（`EXTERNAL_UPDATE_NODE`）。连续外部更新会让 checkpoint 只记录 `__start__`/空 `versions_seen`，LangGraph 无法自动推断写入节点并抛 `InvalidUpdateError`。
```

- [ ] **Step 2: 运行 lint**

Run: `uv run ruff check`

Expected: PASS。

- [ ] **Step 3: 运行全量测试**

Run: `uv run pytest`

Expected: PASS。

- [ ] **Step 4: 快速导入 sanity check**

Run: `uv run python -c "from bot.core.graph import EXTERNAL_UPDATE_NODE; print(EXTERNAL_UPDATE_NODE)"`

Expected: `describe_image`。

- [ ] **Step 5: Commit**

```bash
git add AGENTS.md
git commit -m "docs: document external graph update as_node invariant"
```

---

## Non-Fix Notes

- Milvus `AllocTimestamp Method not implemented!`：milvus-lite gRPC adapter 只实现部分 RPC，pymilvus 对该调用有 `ignore_unimplemented(0)` 回落；后续 `Created milvus collection`、嵌入和检索均正常，因此不调整依赖或日志级别。
- `db/checkpoint.sqlite` 中 `llonebot:914363502:914363502` 已落地的 2 条 HumanMessage 保留即可；Task 1 修复后，后续 context-only 消息会从该 checkpoint 继续追加。
