# BotState 冗余字段清理实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 清理 `object/bot/state.py` 的 BotState 冗余字段——删除 `new_message`/`raw_content`/`session_id`，新增预计算 `clean_text`，并使 `router` 预留节点在新 schema 下自洽。

**Architecture:** 不改图拓扑、不改节点逻辑语义，只动数据字段与消费路径。handler（ingress）预计算 `clean_text` 注入 state；`index_turn` 直接消费该字段，删除图内重复正则解析；`detect_intent` 删除死兜底；`router` 保留为预留节点，仅修两处数据依赖。

**Tech Stack:** Python ≥3.12、uv、pytest、LangGraph 1.2.2、LangChain Core 1.4.9、TypedDict（不引入 Pydantic、不嵌套）。

## Global Constraints

- 运行时行为不变：`llm_text` 每轮必注入（`to_llm_text` 返回 `str`）；`clean_text` 与 `parse_content` 内同一函数，索引输出逐字节一致；`session_id` 只进日志，改打 `thread_id` 无功能损失。
- **不删除** `bot/core/nodes/llm_node/router.py` 与 `common/prompts.py` 的 `ROUTER_PROMPT`——保留为预留节点；仅修正其对 `new_message`/`session_id` 的依赖。
- 不引入 Pydantic State，不做嵌套 State。
- 提交信息遵循仓库惯例（conventional commits，中文描述，如 `refactor:`/`docs:`）。
- 测试命令统一 `uv run pytest <path> -q`；验收 `uv run pytest -q` 全绿。

---

### Task 1: BotState schema 与 make_state 夹具对齐

**Files:**
- Modify: `object/bot/state.py`
- Modify: `tests/fakes.py`

**Interfaces:**
- Produces: 新 BotState schema——删除 `new_message`/`raw_content`/`session_id`，新增 `clean_text: str`。本任务后 `tests/fakes.make_state` 默认不再含三个被删字段、含 `clean_text: "你好"`。TypedDict 无运行时校验，此任务不破坏任何测试（各节点均用 `state.get(x, default)` 防御读取）。

- [ ] **Step 1: 改 `object/bot/state.py` schema**

将文件改为：

```python
from typing import Annotated

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class BotState(TypedDict):
    """State of the conversation graph.

    ``messages`` uses the ``add_messages`` reducer so that each node
    only returns the *new* messages to append. Old messages are
    automatically checkpointed by SqliteSaver.

    ``should_respond`` is set by ``detect_intent`` deterministically
    (text/image reply on DIRECT or @-mention; file/audio/video never
    reply). The LLM ``router_node`` has been unplugged from the graph —
    no downstream node overrides it.

    ``clean_text`` 由 handler 预计算（``parse_content`` → ``ParsedContent.clean_text``）
    注入，供 RAG 索引（``index_turn``）直接消费，避免图内每轮重复解析。
    """
    messages: Annotated[list[BaseMessage], add_messages]
    persona: str
    conversation_summary: str   # progressive summary of older messages (dynamic inject)
    thread_id: str        # checkpoint isolation key = platform:guild:channel
    user_id: str          # 当前消息发送者的用户 ID（记忆工具按用户维度存取）
    reply_text: str
    should_respond: bool
    bot_name: str
    tool_rounds: int       # 工具调用轮次计数（call_llm 递增，工具回环上限）
    # --- Fields for detect_intent node ---
    channel_type: int       # ChannelType enum value (0=TEXT, 1=DIRECT)
    bot_id: str             # bot's own user ID for @-mention detection
    user_name: str          # sender's display name (for group chat attribution)
    # --- Message classification (computed in MessageHandler, ingress) ---
    content_kind: str       # object.bot.content.MessageKind.value: "text"/"image"/"file"/"audio"/"video"
    llm_text: str           # media→占位符、@→@昵称(id)/所有成员 — HumanMessage content
    clean_text: str         # 剥全部标签、unescape、折叠空白（RAG 索引用，handler 预计算）
    image_srcs: list[str]   # 本轮图片 URL（describe_image 视觉理解用）
    vision_desc: str        # 本轮图片描述（RAG 索引；仅 image 轮有效）
    mentions: dict[str, str]   # 顶层 @ 提及 {id: 昵称}（detect_intent 判定用）
```

- [ ] **Step 2: 改 `tests/fakes.py` 的 `make_state`**

`make_state` 的默认 dict 中：删除 `"session_id": "test:session"`、`"new_message": None`、`"raw_content": "你好"`，新增 `"clean_text": "你好"`。改后：

```python
    state = {
        "messages": [],
        "persona": "你是{bot_name}",
        "conversation_summary": "",
        "thread_id": "test:thread",
        "user_id": "u1",
        "reply_text": "",
        "should_respond": True,
        "bot_name": "测试机器人",
        "channel_type": 0,
        "bot_id": "bot1",
        "user_name": "张三",
        "tool_rounds": 0,
        "content_kind": "text",
        "clean_text": "你好",
        "mentions": {},
    }
```

- [ ] **Step 3: 跑受影响测试，确认仍全绿**

Run: `uv run pytest tests/test_detect_intent.py tests/test_graph.py tests/test_handler_media.py tests/test_call_llm_node.py tests/test_describe_image.py tests/test_tool_node.py tests/test_handler.py -q`
Expected: 全 PASS（被删字段各节点均以 `.get` 防御读取，缺省回退安全）。

- [ ] **Step 4: Commit**

```bash
git add object/bot/state.py tests/fakes.py
git commit -m "refactor: BotState 删除 new_message/raw_content/session_id，新增 clean_text（schema + 测试夹具对齐）"
```

---

### Task 2: clean_text 数据流（handler 预计算注入 + index_turn 消费）

**Files:**
- Modify: `bot/handler.py`
- Modify: `bot/core/nodes/action_node/index_turn.py`
- Modify: `tests/test_handler.py`
- Modify: `tests/test_handler_media.py`
- Modify: `tests/test_graph.py`

**Interfaces:**
- Consumes: Task 1 的 BotState schema（`clean_text` 字段、`session_id` 已删）。
- Produces: `MessageHandler._process` 的 ainvoke 输入不再含 `new_message`/`raw_content`/`session_id`，新增 `clean_text: parsed.clean_text`；`index_turn_node` 从 `state["clean_text"]` 取清洗文本（不再图内解析 raw_content）。日志全部改打 `thread_id`。

- [ ] **Step 1: 改 `bot/handler.py`**

a) 删除第 4 行 import：`from langchain_core.messages import HumanMessage`（仅用于被删的 `new_message` 占位符）。

b) 删除第 141 行 `session_id = f"{platform}:{guild_id}:{channel_id}:{user_id}"`。

c) 第 149-152 行日志 `session=%s` 改为仅打 thread：

```python
        logger.info(
            "Processing %s message from %s (thread=%s): %.60s",
            content_kind, user_id, thread_id, raw_content,
        )
```

d) `graph.ainvoke` 的输入 dict 中：删除 `"new_message": HumanMessage(content="")`、`"session_id": session_id`、`"raw_content": raw_content`；新增 `"clean_text": parsed.clean_text`：

```python
            result = await self.graph.ainvoke(
                {
                    "thread_id": thread_id,
                    "persona": self._persona,
                    "reply_text": "",
                    "should_respond": False,  # detect_intent decides
                    "bot_name": self._bot_name or "",
                    "bot_id": self._bot_id or "",
                    "tool_rounds": 0,
                    "user_id": user_id,
                    "channel_type": channel_type,
                    "user_name": user_name,
                    "content_kind": content_kind,
                    "llm_text": parsed.llm_text,
                    "clean_text": parsed.clean_text,
                    "mentions": parsed.mentions,
                    "image_srcs": image_srcs,
                },
                {
                    "configurable": {"thread_id": thread_id},
                    "recursion_limit": recursion_limit,
                },
            )
```

e) 第 190 行异常日志：`"Graph invoke failed for session %s", session_id` → `"Graph invoke failed for thread %s", thread_id`。

（`raw_content = event.message.content or ""` 保留为本地变量，仅用于日志。）

- [ ] **Step 2: 改 `bot/core/nodes/action_node/index_turn.py`**

a) import 行 `from bot.core.utils import MessageKind, clean_text` → `from bot.core.utils import MessageKind`。

b) 取内容行 `content = clean_text(state.get("raw_content", ""))` → `content = state.get("clean_text", "")`。

c) 更新模块 docstring 首段，说明 clean_text 由 handler 预计算注入：

```python
"""index_turn — persist the current turn into the RAG store.

Runs after ``summarize``. It is reached by both replied turns (user +
bot reply, 2 records) and non-replied group text (user only, 1 record —
``bot_reply`` is empty and ``RagService.index_turn`` filters it out).
``clean_text`` 由 handler 预计算注入（``parse_content`` 产出），本节点直接
消费、不再图内解析 raw_content。纯媒体（clean_text 为空）跳过；image 轮
带 ``vision_desc`` 时把描述并入索引内容。
"""
```

- [ ] **Step 3: 改 `tests/test_handler.py`**

在 `test_channel_type_coerced_to_int_before_graph` 末尾追加一行，锁定新 ingress 字段：

```python
    assert graph.state["clean_text"] == "你好"   # 预计算清洗文本注入 state
```

- [ ] **Step 4: 改 `tests/test_handler_media.py`**

全部 `raw_content=` 参数改为 `clean_text=`（值为已清洗文本），文件整体改后：

```python
"""index_turn_node：RAG 索引图节点（消费 handler 预计算的 clean_text；纯媒体跳过、vision 描述并入）。"""

import asyncio

from bot.core.nodes import index_turn_node
from tests.fakes import StubRagService, make_state


def _run(rag, **state):
    asyncio.run(index_turn_node(make_state(**state), rag))


def test_index_turn_noop_when_rag_disabled():
    _run(None, clean_text="你好", reply_text="收到")  # 不抛异常即可


def test_index_turn_indexes_user_message_without_reply():
    rag = StubRagService()
    _run(rag, clean_text="你好", reply_text="")
    assert rag.last_indexed is not None
    assert rag.last_indexed["user_message"] == "你好"
    assert rag.last_indexed["bot_reply"] == ""  # service 层过滤 → 只索引 1 条


def test_index_turn_skips_media_only():
    rag = StubRagService()
    _run(rag, clean_text="", reply_text="收到", content_kind="image")
    assert rag.last_indexed is None


def test_index_turn_uses_precomputed_clean_text():
    rag = StubRagService()
    _run(rag, clean_text="你好", reply_text="收到")
    assert rag.last_indexed is not None
    assert rag.last_indexed["user_message"] == "你好"
    assert rag.last_indexed["bot_reply"] == "收到"


def test_index_turn_keeps_text_beside_image():
    rag = StubRagService()
    _run(rag, clean_text="今天真开心", reply_text="收到")
    assert rag.last_indexed["user_message"] == "今天真开心"


def test_index_turn_unescapes_entities():
    rag = StubRagService()
    _run(rag, clean_text="A & B", reply_text="收到")
    assert rag.last_indexed["user_message"] == "A & B"


def test_index_turn_appends_vision_desc_for_image():
    rag = StubRagService()
    _run(rag, clean_text="", reply_text="收到",
         content_kind="image", vision_desc="一只猫")
    assert rag.last_indexed is not None
    assert rag.last_indexed["user_message"] == "[图片：一只猫]"


def test_index_turn_image_without_vision_skips():
    rag = StubRagService()
    _run(rag, clean_text="", reply_text="收到",
         content_kind="image")
    assert rag.last_indexed is None


def test_index_turn_text_ignores_stale_vision_desc():
    rag = StubRagService()
    # text 轮残留上一张图的 vision_desc → content_kind=="text" 过滤，不追加
    _run(rag, clean_text="晚上吃什么", reply_text="去吃火锅",
         content_kind="text", vision_desc="一只猫")
    assert rag.last_indexed is not None
    assert rag.last_indexed["user_message"] == "晚上吃什么"
```

（注：`test_index_turn_strips_at_mention` 合并进 `test_index_turn_uses_precomputed_clean_text`——at 剥离已前置到 handler 的 `clean_text`，index_turn 不再负责；`test_index_turn_unescapes_entities` 同样反映"消费预计算值"。原 `test_index_turn_strips_at_mention` 与 `test_index_turn_uses_precomputed_clean_text` 输入等价，合并为一个。）

- [ ] **Step 5: 改 `tests/test_graph.py`**

a) `_initial_state()` 改为（删 `new_message`/`session_id`/`raw_content`，加 `llm_text`/`clean_text`）：

```python
def _initial_state() -> dict:
    # channel_type=1 (DIRECT) → detect_intent 置 should_respond=True → call_llm
    return {
        "thread_id": "test:thread",
        "persona": "你是{bot_name}",
        "reply_text": "",
        "should_respond": False,
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

b) `test_group_non_mention_text_indexes_without_reply` 的 state：`"raw_content": "晚上吃什么"` → `"clean_text": "晚上吃什么"`（`llm_text` 已在该处显式给出，保留）。

c) `test_group_non_mention_image_ends_without_index` 的 state：删 `"raw_content": '<img src="x"/>'`，加 `"clean_text": ""`。

d) `test_private_file_ends_without_reply` 的 state：删 `"raw_content": '<file src="x"/>'`，加 `"clean_text": ""`。

e) `test_graph_image_reply_includes_vision_description` 与 `test_graph_image_reply_without_vision_keeps_placeholder` 的 state：删 `"raw_content": '<img src="https://x/1.jpg"/>'`，加 `"clean_text": ""`。

- [ ] **Step 6: 跑受影响测试**

Run: `uv run pytest tests/test_handler.py tests/test_handler_media.py tests/test_graph.py -q`
Expected: 全 PASS（`test_graph_image_reply_*` 的 `rag.last_indexed` 断言依赖 `clean_text="" + vision_desc` 的拼装路径，应保持绿）。

- [ ] **Step 7: Commit**

```bash
git add bot/handler.py bot/core/nodes/action_node/index_turn.py tests/test_handler.py tests/test_handler_media.py tests/test_graph.py
git commit -m "refactor: handler 预计算注入 clean_text，index_turn 消费之，日志改打 thread_id"
```

---

### Task 3: detect_intent 移除死兜底与 new_message

**Files:**
- Modify: `bot/core/nodes/action_node/detect_intent.py`
- Modify: `tests/test_detect_intent.py`

**Interfaces:**
- Consumes: Task 1/2 的 schema（无 `raw_content`/`new_message`；`llm_text` 每轮必注入）。
- Produces: `detect_intent` 不再返回 `new_message` 键、不再读 `raw_content`；`content = state.get("llm_text", "")`。删除模块内 `_strip_mention`。

- [ ] **Step 1: 改 `bot/core/nodes/action_node/detect_intent.py`**

a) 删除 `_strip_mention` 函数（第 23-28 行）。

b) `detect_intent` 体内：删 `raw_content = state.get("raw_content", "")`；把 `content = state.get("llm_text")` 和 `if content is None: content = _strip_mention(raw_content)` 两行合并为 `content = state.get("llm_text", "")`。

c) return 从 `{"should_respond": ..., "new_message": new_message, "messages": ...}` 改为：

```python
    return {
        "should_respond": should_respond,
        "messages": [message] if add_to_context else [],
    }
```

（局部变量 `new_message` 改名 `message`。）

d) 更新模块 docstring：末行 "HumanMessage 用 handler 注入的 llm_text，raw_content 兜底" → "HumanMessage 内容用 handler 注入的 llm_text（每轮必注入，无兜底）"。

改后 `detect_intent` 函数体：

```python
    channel_type = state.get("channel_type", 0)
    bot_id = state.get("bot_id", "")
    bot_name = state.get("bot_name", "")
    mentions = state.get("mentions", {})
    user_name = state.get("user_name", "")
    content_kind = state.get("content_kind", "")

    # 判定表（decide_reply / keep_in_context）单一来源见 bot.core.utils.routing
    should_respond = decide_reply(channel_type, content_kind, bot_id, bot_name, mentions)

    # 2) Build HumanMessage: handler 每轮必注入 llm_text（媒体->占位符、@ 已渲染）
    content = state.get("llm_text", "")
    is_group = channel_type != ChannelType.DIRECT
    if is_group and user_name:
        message = HumanMessage(content=content, name=user_name)
    else:
        message = HumanMessage(content=content)

    # 3) Non-replied media must NOT enter context — its placeholder would
    #    pollute later @-mention turns. Keep them out of ``messages``.
    add_to_context = keep_in_context(should_respond, content_kind)
    logger.debug(
        "detect_intent: should_respond=%s channel_type=%s content_kind=%s add_to_context=%s",
        should_respond, channel_type, content_kind, add_to_context,
    )
    return {
        "should_respond": should_respond,
        "messages": [message] if add_to_context else [],
    }
```

- [ ] **Step 2: 改 `tests/test_detect_intent.py`**

a) 删除 `test_falls_back_to_mention_strip_when_llm_text_absent`，替换为锁定新行为的用例：

```python
def test_absent_llm_text_yields_empty_content():
    state = make_state(
        channel_type=0,
        bot_id="bot1",
        user_name="",
    )
    result = asyncio.run(detect_intent(state))
    assert result["messages"][0].content == ""
```

b) 其余用例删除不再生效的 `raw_content=` 覆盖；对内容有意义的用例补 `llm_text=`：
- `test_uses_llm_text_when_present`：删 `raw_content=`（保留 `llm_text`）
- `test_image_only_empty_llm_text_is_preserved`：删 `raw_content=`（保留 `llm_text=""`）
- `test_group_without_mention_does_not_respond` / `test_group_text_without_mention_added_to_context`：删 `raw_content=`（保留 `llm_text`）
- `test_group_at_mention_responds`：删 `raw_content='<at id="bot1" name="Bot"/> 你好'`，加 `llm_text="你好"`
- `test_media_never_responds_even_in_direct` / `test_media_never_responds_even_with_mention` / `test_image_in_group_without_at_does_not_respond`：删 `raw_content=`（不补 llm_text，断言只看 should_respond/messages）
- `test_image_in_direct_responds`：删 `raw_content=`，加 `llm_text=""`
- `test_group_name_only_mention_responds_with_empty_bot_id`：删 `raw_content=`（保留 `llm_text`）

c) 更新模块 docstring：末行 "HumanMessage 用 handler 注入的 llm_text，raw_content 兜底" → "HumanMessage 用 handler 注入的 llm_text（每轮必注入，无兜底）"。

- [ ] **Step 3: 跑测试**

Run: `uv run pytest tests/test_detect_intent.py -q`
Expected: 全 PASS，且不再存在对 raw_content 兜底的引用。

- [ ] **Step 4: Commit**

```bash
git add bot/core/nodes/action_node/detect_intent.py tests/test_detect_intent.py
git commit -m "refactor: detect_intent 移除 _strip_mention 兜底与 new_message，llm_text 直接消费"
```

---

### Task 4: router 预留节点数据依赖修正

**Files:**
- Modify: `bot/core/nodes/llm_node/router.py`

**Interfaces:**
- Consumes: 新 BotState schema（`new_message`/`session_id` 已删）。
- Produces: `router_node` 读 `state.get("llm_text", "")` 与 `state.get("thread_id", "")`，与当前图（detect_intent 先行、llm_text 必注入）自洽；`ROUTER_PROMPT` 保留不动。本节点未接线，无直接单测，靠全量回归。

- [ ] **Step 1: 改 `bot/core/nodes/llm_node/router.py`**

`HumanMessage(content=f"消息内容：{state['new_message'].content}")` → `HumanMessage(content=f"消息内容：{state.get('llm_text', '')}")`；异常日志 `state["session_id"]` → `state.get("thread_id", "")`。改后关键段：

```python
        prompt = ROUTER_PROMPT.format(bot_name=state.get("bot_name", ""))
        try:
            response = await llm.ainvoke([
                SystemMessage(content=prompt),
                HumanMessage(content=f"消息内容：{state.get('llm_text', '')}"),
            ])
            should_respond = "true" in response.content.strip().lower()
        except Exception:
            logger.warning("Router LLM call failed for thread %s", state.get("thread_id", ""))
            should_respond = False
```

- [ ] **Step 2: 语法检查**

Run: `uv run python -c "from bot.core.nodes.llm_node.router import router_node; print('router OK')"`
Expected: 打印 `router OK`（无 import/语法错误）。

- [ ] **Step 3: Commit**

```bash
git add bot/core/nodes/llm_node/router.py
git commit -m "refactor: router 预留节点依赖改为 llm_text/thread_id（新 BotState schema 自洽）"
```

---

### Task 5: 日志统一改打 thread_id（call_llm / summarize / tool_node）

**Files:**
- Modify: `bot/core/nodes/llm_node/call_llm.py`
- Modify: `bot/core/nodes/action_node/summarize.py`
- Modify: `bot/core/nodes/tool_node/tool_node.py`

**Interfaces:**
- Consumes: 新 BotState schema（`session_id` 已删）。若这三节点仍 `state.get("session_id")` 将得空串——日志质量回归，必须改打 `thread_id`。
- Produces: `call_llm`/`summarize`/`tool_node` 的日志统一打 `thread_id`；`_log_llm_error` 参数与日志文案改名。

- [ ] **Step 1: 改 `bot/core/nodes/llm_node/call_llm.py`**

两处调用（第 55、81 行）`_log_llm_error(exc, state.get("session_id", ""))` → `_log_llm_error(exc, state.get("thread_id", ""))`；`_log_llm_error` 定义改为：

```python
def _log_llm_error(exc: Exception, thread_id: str) -> None:
    if isinstance(exc, TimeoutError) or "Timeout" in type(exc).__name__:
        logger.warning("LLM call timed out for thread %s", thread_id)
    else:
        logger.exception("LLM call failed for thread %s", thread_id)
```

- [ ] **Step 2: 改 `bot/core/nodes/action_node/summarize.py`**

四处日志的 `session_id` 全部改为 `thread_id`（`state.get("session_id", "")` → `state.get("thread_id", "")`，文案 `session=%s`/`for session %s` → `thread=%s`/`for thread %s`）：

- 第 43-46 行 `logger.debug("summarize check: total=%d trigger=%d session=%s", total, trigger, state.get("session_id", ""))`
- 第 67-70 行 `logger.info("Summarizing %d messages (keeping %d) for session %s", ...)`
- 第 98 行 `logger.exception("Summary generation failed for session %s", state.get("session_id"))`（注意此处 `state.get` 无默认值，统一补 `, "")`）
- 第 104-107 行 `logger.info("Summary generated: %d chars, removed %d messages for session %s", ...)`

- [ ] **Step 3: 改 `bot/core/nodes/tool_node/tool_node.py`**

第 48 行 `logger.exception("Tool %s failed for session %s", name, state.get("session_id", ""))` → `logger.exception("Tool %s failed for thread %s", name, state.get("thread_id", ""))`。

- [ ] **Step 4: 回归验证**

Run: `uv run pytest tests/test_call_llm_node.py tests/test_tool_node.py tests/test_graph.py -q`
Expected: 全 PASS（日志文案不参与断言，仅确认无回归）。

- [ ] **Step 5: Commit**

```bash
git add bot/core/nodes/llm_node/call_llm.py bot/core/nodes/action_node/summarize.py bot/core/nodes/tool_node/tool_node.py
git commit -m "refactor: 节点日志 session_id → thread_id（call_llm/summarize/tool_node）"
```

---

### Task 6: 全量回归 + CLAUDE.md 同步

**Files:**
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: Task 1-4 的全部改动。
- Produces: 验收 `uv run pytest -q` 全绿；CLAUDE.md 与新 schema 一致。

- [ ] **Step 1: 全量测试**

Run: `uv run pytest -q`
Expected: 全 PASS（无 fail/error）。

- [ ] **Step 2: 同步 `CLAUDE.md`**

a) 第 81 行 `- **session_id** = `platform:guild:channel:user` — used for logging` → 标记已移除：

```markdown
- **session_id**（已从 BotState 移除） = `platform:guild:channel:user` — 曾仅用于日志；现日志改打 thread_id，省 checkpoint 冗余
```

b) 第 125 行 RAG 索引段，`用户内容先经 `clean_text` 清洗（剥全部元素标签 + unescape）` → 改为：

```markdown
用户内容来自 handler 预计算的 `clean_text`（剥全部元素标签 + unescape，`parse_content` 产出），`index_turn` 直接消费、不再图内解析 raw_content
```

c) 第 153 行 @-mention Gotcha，删除尾句 `；`detect_intent._strip_mention` 仅在 state 无 `llm_text` 时兜底``，改为 `；`llm_text` 由 handler 每轮必注入，detect_intent 直接消费（无兜底）``。

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: CLAUDE.md 同步 BotState 字段清理（session_id 移除、clean_text 预计算、无 _strip_mention 兜底）"
```
