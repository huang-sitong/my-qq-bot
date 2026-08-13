# Auto Reply Routing & Random Response Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按新的回复判定树处理 @/私聊、无@群聊、auto_reply 随机回复和图片占位，使显式请求始终回复、非显式 auto_reply 只按概率和冷却回复，同时不丢失上下文与 RAG 索引。

**Architecture:** 在 `routing.py` 增加显式请求判定和 `has_text` 路由；新增纯函数 `reply_policy.py` 负责 auto_reply 随机/冷却；`MessageHandler` 在入图前计算本轮是否允许 auto_reply，并把该值作为 state 的 `auto_reply`；`detect_intent` 只消费该结果；`index_turn` 对图片统一写 `[图片]` 占位符。

**Tech Stack:** Python >=3.12、LangGraph、pydantic-settings、pytest、ruff。

## Global Constraints

- Python >=3.12，使用 `uv run` 执行测试和 lint。
- 图片占位符统一引用 `IMAGE_PLACEHOLDER`（当前值为 `[图片]`），不在代码里散落 `[image]`/`[图片]` 字符串。
- `file/audio/video` 永不回复、永不入上下文、永不入 RAG，即使私聊/@。
- 私聊和群聊顶层 @ 属于显式请求，绕过 random 和 cooldown。
- 斜杠指令保持现有“图外直接处理、不进图”的行为，不参与新判定树。
- 不删除用户未提交的工作区改动；提交时只提交本任务涉及的文件。
- 当前完整 `pytest` 存在与本次功能无关的既有失败（默认模型值、`/clear` 文案、send_file 临时目录白名单）。执行本计划时以任务内的目标测试为准，发现无关失败只记录不修改。

## File Structure

- `common/config.py`：新增 auto_reply 随机概率和冷却配置。
- `bot/core/utils/routing.py`：显式请求判定、`has_text` 上下文/路由。
- `bot/core/utils/reply_policy.py`：auto_reply 随机/冷却纯函数。
- `bot/handler.py`：入图前计算 `auto_reply` 是否允许，维护 per-thread 冷却。
- `bot/core/nodes/action_node/detect_intent.py`：消费 `has_text` 和允许后的 `auto_reply`。
- `bot/core/graph.py`：`_route_after_detect` 透传 `has_text`。
- `bot/core/nodes/action_node/index_turn.py`：图片 RAG 统一写占位符。
- `object/bot/state.py`：新增 `has_text` 状态字段。
- `.env-template`、`README.md`、`AGENTS.md`：同步新配置和行为说明。
- 测试：`tests/test_config.py`、`tests/test_routing.py`、`tests/test_detect_intent.py`、`tests/test_handler.py`、`tests/test_index_turn.py`、`tests/test_handler_media.py`、`tests/test_graph.py`，新增 `tests/test_reply_policy.py`。

---

### Task 1: 配置、显式请求判定和 auto_reply 策略纯函数

**Files:**
- Modify: `common/config.py:259-262`
- Modify: `bot/core/utils/routing.py:19-46`
- Create: `bot/core/utils/reply_policy.py`
- Test: `tests/test_config.py`
- Test: `tests/test_routing.py`
- Test: `tests/test_reply_policy.py`

**Interfaces:**
- Consumes: 现有 `ChannelType`、`BotConfig`。
- Produces: `is_explicit_request(channel_type, bot_id, bot_name, mentions) -> bool`
- Produces: `should_allow_auto_reply(channel_type, mentions, bot_id, bot_name, auto_reply_enabled, cooldown_elapsed, random_value, rate) -> bool`
- Produces: 新配置字段 `auto_reply_random_rate: float`、`auto_reply_cooldown: int`。

- [ ] **Step 1: 新增配置字段**

在 `common/config.py` 的 `auto_reply` 字段后新增：

```python
    auto_reply_random_rate: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        validation_alias="BOT_AUTO_REPLY_RANDOM_RATE",
    )
    auto_reply_cooldown: int = Field(
        default=30,
        ge=0,
        validation_alias="BOT_AUTO_REPLY_COOLDOWN",
    )
```

- [ ] **Step 2: 更新配置测试**

在 `tests/test_config.py` 的 `EXPECTED_DEFAULTS` 中新增：

```python
    "auto_reply_random_rate": 0.3,
    "auto_reply_cooldown": 30,
```

在 `ENV_SAMPLES` 中新增：

```python
    "auto_reply_random_rate": ("0.5", 0.5),
    "auto_reply_cooldown": ("10", 10),
```

新增校验测试：

```python
def test_invalid_auto_reply_random_rate_rejected(monkeypatch):
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("BOT_AUTO_REPLY_RANDOM_RATE", "1.1")
    with pytest.raises(ValidationError):
        BotConfig(_env_file=None)


def test_invalid_auto_reply_cooldown_rejected(monkeypatch):
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("BOT_AUTO_REPLY_COOLDOWN", "-1")
    with pytest.raises(ValidationError):
        BotConfig(_env_file=None)
```

- [ ] **Step 3: 运行配置测试确认新字段生效**

Run: `uv run python -m pytest tests/test_config.py -k "auto_reply_random_rate or auto_reply_cooldown" -q`
Expected: PASS

- [ ] **Step 4: 在 `routing.py` 增加显式请求判定**

```python
def is_explicit_request(channel_type: int, bot_id: str, bot_name: str, mentions: dict) -> bool:
    """私聊或群聊顶层@bot 视为显式请求，永远绕过 auto_reply 随机门。"""
    if channel_type == ChannelType.DIRECT:
        return True
    mentioned_names = set(mentions.values())
    return bool(bot_id in mentions or (bot_name and bot_name in mentioned_names))
```

- [ ] **Step 5: 新增路由测试**

在 `tests/test_routing.py` 中新增：

```python
from bot.core.utils.routing import (
    decide_reply,
    is_explicit_request,
    keep_in_context,
    route_after_detect,
)


def test_is_explicit_request_direct_true():
    assert is_explicit_request(ChannelType.DIRECT, "bot1", "Bot", {}) is True


def test_is_explicit_request_mention_true():
    assert is_explicit_request(ChannelType.TEXT, "bot1", "Bot", {"bot1": "小助手"}) is True


def test_is_explicit_request_group_non_mention_false():
    assert is_explicit_request(ChannelType.TEXT, "bot1", "Bot", {}) is False
```

- [ ] **Step 6: 创建 `reply_policy.py`**

```python
"""auto_reply 随机/冷却纯函数；random_value 由调用方注入，保持可测试。"""

from bot.core.utils.routing import is_explicit_request


def should_allow_auto_reply(
    channel_type: int,
    mentions: dict[str, str],
    bot_id: str,
    bot_name: str,
    auto_reply_enabled: bool,
    cooldown_elapsed: bool,
    random_value: float,
    rate: float,
) -> bool:
    if not auto_reply_enabled:
        return False
    if is_explicit_request(channel_type, bot_id, bot_name, mentions):
        return False
    if not cooldown_elapsed:
        return False
    return random_value < rate
```

- [ ] **Step 7: 新增 `tests/test_reply_policy.py`**

```python
"""auto_reply 随机/冷却策略纯函数测试。"""

from bot.core.utils.reply_policy import should_allow_auto_reply
from object.satori import ChannelType


def test_disabled_never_allows():
    assert should_allow_auto_reply(
        ChannelType.TEXT, {}, "bot1", "Bot", False, True, 0.0, 0.3,
    ) is False


def test_explicit_direct_bypasses_gate():
    assert should_allow_auto_reply(
        ChannelType.DIRECT, {}, "bot1", "Bot", True, True, 0.9, 0.3,
    ) is False


def test_explicit_mention_bypasses_gate():
    assert should_allow_auto_reply(
        ChannelType.TEXT, {"bot1": "Bot"}, "bot1", "Bot", True, True, 0.9, 0.3,
    ) is False


def test_cooldown_blocks_auto_reply():
    assert should_allow_auto_reply(
        ChannelType.TEXT, {}, "bot1", "Bot", True, False, 0.0, 0.3,
    ) is False


def test_random_below_rate_allows():
    assert should_allow_auto_reply(
        ChannelType.TEXT, {}, "bot1", "Bot", True, True, 0.1, 0.3,
    ) is True


def test_random_above_rate_blocks():
    assert should_allow_auto_reply(
        ChannelType.TEXT, {}, "bot1", "Bot", True, True, 0.5, 0.3,
    ) is False
```

- [ ] **Step 8: 运行新增策略测试**

Run: `uv run python -m pytest tests/test_reply_policy.py -q`
Expected: 6 passed

- [ ] **Step 9: Commit**

```bash
git add common/config.py bot/core/utils/routing.py bot/core/utils/reply_policy.py tests/test_config.py tests/test_routing.py tests/test_reply_policy.py
git commit -m "feat: add auto reply random and cooldown policy"
```

---

### Task 2: 新判定树进入 routing / state / detect_intent / graph

**Files:**
- Modify: `bot/core/utils/routing.py:33-46`
- Modify: `object/bot/state.py:37-46`
- Modify: `bot/core/nodes/action_node/detect_intent.py:37-60`
- Modify: `bot/core/graph.py:48-59`
- Test: `tests/test_routing.py`
- Test: `tests/test_detect_intent.py`
- Test: `tests/test_graph.py`

**Interfaces:**
- Consumes: `has_text` state field、`is_explicit_request`。
- Produces: `keep_in_context(should_respond, content_kind, has_text=False) -> bool`
- Produces: `route_after_detect(should_respond, content_kind, has_text=False) -> str | None`

- [ ] **Step 1: 修改 `routing.py` 的上下文和路由函数**

```python
def keep_in_context(should_respond: bool, content_kind: str, has_text: bool = False) -> bool:
    """回复轮必入上下文；非回复文本/图文混合入上下文；纯媒体不入。"""
    return should_respond or content_kind == MessageKind.TEXT.value or has_text


def route_after_detect(should_respond: bool, content_kind: str, has_text: bool = False) -> str | None:
    """回复 → describe_image；可入上下文的非回复文本/图文混合 → summarize；其余 END。"""
    if should_respond:
        return "describe_image"
    if content_kind == MessageKind.TEXT.value or has_text:
        return "summarize"
    return None
```

- [ ] **Step 2: 更新路由测试**

在 `tests/test_routing.py` 中新增/更新：

```python
def test_keep_mixed_image_text_without_reply():
    assert keep_in_context(False, "image", has_text=True) is True


def test_not_keep_pure_image_without_text():
    assert keep_in_context(False, "image", has_text=False) is False


def test_non_reply_mixed_image_text_routes_to_summarize():
    assert route_after_detect(False, "image", has_text=True) == "summarize"
```

- [ ] **Step 3: 给 `BotState` 增加 `has_text`**

在 `object/bot/state.py` 的消息分类字段中新增：

```python
    has_text: bool         # handler 从 ParsedContent.has_text 注入，供图文混合入上下文
```

- [ ] **Step 4: 修改 `detect_intent`**

```python
    has_text = state.get("has_text", False)
    should_respond = decide_reply(
        channel_type, content_kind, bot_id, bot_name, mentions, auto_reply,
    )
    ...
    add_to_context = keep_in_context(should_respond, content_kind, has_text)
```

并更新日志：

```python
    logger.debug(
        "detect_intent: should_respond=%s channel_type=%s content_kind=%s has_text=%s add_to_context=%s",
        should_respond, channel_type, content_kind, has_text, add_to_context,
    )
```

- [ ] **Step 5: 更新 `detect_intent` 测试**

新增：

```python
def test_group_image_with_text_without_at_added_to_context():
    state = make_state(
        llm_text="看图 [图片]",
        clean_text="看图",
        content_kind="image",
        channel_type=0,
        bot_id="bot1",
        has_text=True,
    )
    result = asyncio.run(detect_intent(state))
    assert result["should_respond"] is False
    assert len(result["messages"]) == 1
```

现有 `test_image_in_group_without_at_does_not_respond` 继续锁定“纯图片无文本不入上下文”。

- [ ] **Step 6: 修改 `graph.py` 路由**

```python
    return route_after_detect(
        state.get("should_respond", False),
        state.get("content_kind", ""),
        state.get("has_text", False),
    ) or END
```

- [ ] **Step 7: 新增 graph 集成测试**

在 `tests/test_graph.py` 新增：

```python
def test_group_non_mention_image_text_indexes_without_reply(tmp_path):
    rag = StubRagService()
    graph, _ = asyncio.run(
        create_graph(ScriptedLLM([]), BotConfig(rag_enabled=True), db_dir=str(tmp_path), rag_service=rag)
    )
    state = {
        **_initial_state(),
        "channel_type": 0,
        "content_kind": "image",
        "clean_text": "看看这张图",
        "llm_text": "看看这张图 [图片]",
        "has_text": True,
    }
    result = asyncio.run(graph.ainvoke(state, {"configurable": {"thread_id": "test:thread"}}))

    assert result["reply_text"] == ""
    assert rag.last_indexed is not None
    assert rag.last_indexed["user_message"] == "看看这张图"
```

- [ ] **Step 8: 运行相关测试**

Run: `uv run python -m pytest tests/test_routing.py tests/test_detect_intent.py tests/test_graph.py -q`
Expected: 通过；如果 `test_graph_runs_send_file_tool` 失败，它属于 Global Constraints 中记录的既有无关失败。

- [ ] **Step 9: Commit**

```bash
git add bot/core/utils/routing.py object/bot/state.py bot/core/nodes/action_node/detect_intent.py bot/core/graph.py tests/test_routing.py tests/test_detect_intent.py tests/test_graph.py
git commit -m "feat: route mixed image text into context and index"
```

---

### Task 3: Handler 计算 auto_reply 随机门和 cooldown

**Files:**
- Modify: `bot/handler.py:1-55,138-252`
- Test: `tests/test_handler.py`

**Interfaces:**
- Consumes: `should_allow_auto_reply`、`BotConfig.auto_reply_random_rate`、`BotConfig.auto_reply_cooldown`。
- Produces: 图 state 中 `auto_reply` 表示“本轮是否实际允许 auto_reply”；显式请求始终为 `False`。
- Produces: `has_text` state 注入。

- [ ] **Step 1: 修改 handler 导入和初始化**

```python
import random
import time
...
from bot.core.utils.reply_policy import should_allow_auto_reply
```

在 `__init__` 中新增：

```python
        self._last_auto_reply_at: dict[str, float] = {}
        self._random = random.Random()
```

- [ ] **Step 2: 新增 `_auto_reply_allowed` 方法**

```python
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
```

- [ ] **Step 3: 在 `_process` 计算 `has_text` 和 `auto_reply_allowed`**

在 `parsed = parse_content(raw_content)` 之后新增：

```python
        has_text = parsed.has_text
        auto_reply_allowed = self._auto_reply_allowed(
            thread_id=thread_id,
            channel_type=channel_type,
            bot_id=self._bot_id or "",
            bot_name=self._bot_name or "",
            mentions=parsed.mentions,
        )
```

把图 state 的 `auto_reply` 改为：

```python
                    "auto_reply": auto_reply_allowed,
```

并在 state 中新增：

```python
                    "has_text": has_text,
```

- [ ] **Step 4: 回复后更新 cooldown**

在 `_process` 的 `if reply_text:` 发送块后新增：

```python
        if reply_text and auto_reply_allowed:
            self._last_auto_reply_at[thread_id] = time.monotonic()
```

- [ ] **Step 5: 新增/更新 handler 测试**

在 `tests/test_handler.py` 顶部新增固定随机源：

```python
class _FixedRandom:
    def __init__(self, value):
        self._value = value

    def random(self):
        return self._value
```

新增测试：

```python
def test_group_non_at_auto_reply_allowed_when_random_hits():
    graph = _StubGraph()
    config = BotConfig(
        _env_file=None,
        auto_reply=True,
        auto_reply_random_rate=1.0,
        auto_reply_cooldown=0,
    )
    handler = _make_handler(graph, bot_config=config)
    handler._random = _FixedRandom(0.1)
    event = EventBody(
        id=10,
        sn=10,
        type="message-created",
        platform="llonebot",
        channel=Channel(id="g1", type=ChannelType.TEXT),
        user=User(id="u2", name="tester"),
        message=Message(id="m10", content="晚上吃什么"),
    )
    asyncio.run(handler._process({
        "event": event,
        "platform": "llonebot",
        "guild_id": "g1",
        "channel_id": "g1",
        "user_id": "u2",
        "thread_id": "llonebot:g1:g1",
    }))
    assert graph.state["auto_reply"] is True
    assert graph.state["has_text"] is True
```

把现有 `test_auto_reply_injected_into_graph_state` 改为断言私聊显式请求不会被 random 门标为 auto_reply：

```python
def test_auto_reply_private_explicit_is_not_marked_auto_reply():
    graph = _StubGraph()
    config = BotConfig(_env_file=None, auto_reply=True)
    handler = _make_handler(graph, bot_config=config)
    asyncio.run(handler._process({
        "event": _private_event(),
        "platform": "llonebot",
        "guild_id": "",
        "channel_id": "ch1",
        "user_id": "u1",
        "thread_id": "llonebot::private:ch1",
    }))
    assert graph.state["auto_reply"] is False
```

新增冷却测试：

```python
def test_auto_reply_cooldown_blocks_second_reply():
    graph = _StubGraph()
    config = BotConfig(
        _env_file=None,
        auto_reply=True,
        auto_reply_random_rate=1.0,
        auto_reply_cooldown=60,
    )
    handler = _make_handler(graph, bot_config=config)
    handler._random = _FixedRandom(0.1)
    handler._last_auto_reply_at["llonebot:g1:g1"] = 1e18

    event = EventBody(
        id=11,
        sn=11,
        type="message-created",
        platform="llonebot",
        channel=Channel(id="g1", type=ChannelType.TEXT),
        user=User(id="u2", name="tester"),
        message=Message(id="m11", content="晚上吃什么"),
    )
    asyncio.run(handler._process({
        "event": event,
        "platform": "llonebot",
        "guild_id": "g1",
        "channel_id": "g1",
        "user_id": "u2",
        "thread_id": "llonebot:g1:g1",
    }))
    assert graph.state["auto_reply"] is False
```

- [ ] **Step 6: 运行 handler 测试**

Run: `uv run python -m pytest tests/test_handler.py -q`
Expected: 通过；不通过时先检查 `_FixedRandom` 是否注入到 `_random` 实例。

- [ ] **Step 7: Commit**

```bash
git add bot/handler.py tests/test_handler.py
git commit -m "feat: gate auto reply with random and cooldown"
```

---

### Task 4: RAG 图片统一写入占位符

**Files:**
- Modify: `bot/core/nodes/action_node/index_turn.py:1-42`
- Test: `tests/test_index_turn.py`
- Test: `tests/test_handler_media.py`
- Test: `tests/test_graph.py`

**Interfaces:**
- Consumes: `IMAGE_PLACEHOLDER`、state `content_kind`。
- Produces: 图片轮的 RAG `user_message` 统一为 `clean_text + " " + IMAGE_PLACEHOLDER`。

- [ ] **Step 1: 修改 `index_turn.py`**

```python
from bot.core.utils import IMAGE_PLACEHOLDER, MessageKind, content_to_text
...
    content = state.get("clean_text", "")
    if state.get("content_kind") == MessageKind.IMAGE.value:
        content = f"{content} {IMAGE_PLACEHOLDER}".strip()
    reply_text = content_to_text(state.get("reply_text", "")).strip()
```

删除 `vision_desc` 分支，因为 RAG 按需求只存占位符，不存本地视觉描述。

- [ ] **Step 2: 更新 `tests/test_index_turn.py`**

```python
def test_image_with_vision_desc_indexes_placeholder_only():
    indexed = _run(_img_state(vision_desc="一只橘猫", reply_text="图里是猫"), StubRagService())
    assert indexed["user_message"] == "[图片]"
    assert indexed["bot_reply"] == "图里是猫"


def test_image_text_with_desc_indexes_placeholder_only():
    indexed = _run(
        _img_state(clean_text="帮我看看这张图", vision_desc="一只橘猫", reply_text="是猫"),
        StubRagService(),
    )
    assert indexed["user_message"] == "帮我看看这张图 [图片]"


def test_pure_image_no_desc_but_reply_indexes_placeholder_and_reply():
    rag = StubRagService()
    indexed = _run(_img_state(reply_text="图里是一只橘猫在晒太阳"), rag)
    assert indexed["user_message"] == "[图片]"
    assert indexed["bot_reply"] == "图里是一只橘猫在晒太阳"
```

- [ ] **Step 3: 更新 `tests/test_handler_media.py`**

```python
def test_index_turn_appends_image_placeholder_for_image():
    rag = StubRagService()
    _run(rag, clean_text="", reply_text="收到",
         content_kind="image", vision_desc="一只猫")
    assert rag.last_indexed is not None
    assert rag.last_indexed["user_message"] == "[图片]"
```

现有 `test_index_turn_media_only_with_reply_indexes_reply` 也应改为断言 `user_message == "[图片]"`。

- [ ] **Step 4: 更新 graph 集成测试中的 RAG 断言**

把 Task 2 新增的 `test_group_non_mention_image_text_indexes_without_reply` 断言改为：

```python
    assert rag.last_indexed is not None
    assert rag.last_indexed["user_message"] == "看看这张图 [图片]"
```

- [ ] **Step 5: 运行索引测试**

Run: `uv run python -m pytest tests/test_index_turn.py tests/test_handler_media.py -q`
Expected: 通过

- [ ] **Step 6: Commit**

```bash
git add bot/core/nodes/action_node/index_turn.py tests/test_index_turn.py tests/test_handler_media.py tests/test_graph.py
git commit -m "feat: index image turns with placeholder only"
```

---

### Task 5: 文档、模板和最终验证

**Files:**
- Modify: `.env-template`
- Modify: `README.md`
- Modify: `AGENTS.md`
- Test: `tests/test_config.py`

- [ ] **Step 1: 更新 `.env-template`**

在 `# --- Reply behavior ---` 下新增：

```dotenv
# BOT_AUTO_REPLY_RANDOM_RATE = 0.3   # auto_reply 非@消息的回复概率（0-1）
# BOT_AUTO_REPLY_COOLDOWN = 30       # 同一 thread 两次 auto_reply 最小间隔秒数
```

- [ ] **Step 2: 更新 `README.md` 配置表**

新增两行：

```markdown
| `BOT_AUTO_REPLY_RANDOM_RATE` | auto_reply 非@消息的随机回复概率，默认 `0.3` |
| `BOT_AUTO_REPLY_COOLDOWN` | 同一会话两次 auto_reply 的最小间隔秒数，默认 `30` |
```

- [ ] **Step 3: 更新 `AGENTS.md` 回复判定树说明**

将 `**回复判定树（纯确定性，无 LLM router）**` 条目改为：

```markdown
- **回复判定树（纯确定性，无 LLM router）**：私聊/顶层@为显式请求，始终回复并绕过 auto_reply random/cooldown；file/audio/video 永不回复；群聊非@文本和图文混合在 auto_reply=false 时入上下文+索引但不回复，纯图片无文本忽略；auto_reply=true 时由 `BOT_AUTO_REPLY_RANDOM_RATE` + `BOT_AUTO_REPLY_COOLDOWN` 决定是否回复，未命中仍保留上下文/RAG。图片 RAG 统一使用 `[图片]` 占位符，不存 URL/base64/视觉描述。
```

- [ ] **Step 4: 运行配置模板一致性测试**

Run: `uv run python -m pytest tests/test_config.py -k "env_alias or env_template" -q`
Expected: 通过（已记录的默认模型值失败除外，不修改）。

- [ ] **Step 5: 运行目标测试集**

Run:

```bash
uv run python -m pytest tests/test_reply_policy.py tests/test_routing.py tests/test_detect_intent.py tests/test_describe_image.py tests/test_index_turn.py tests/test_handler_media.py tests/test_handler.py tests/test_graph.py -q
```

Expected: 除 Global Constraints 已记录的 `test_graph_runs_send_file_tool` 外全部通过。

- [ ] **Step 6: 运行 ruff**

Run: `uv run ruff check`
Expected: All checks passed

- [ ] **Step 7: Commit**

```bash
git add .env-template README.md AGENTS.md tests/test_config.py
git commit -m "docs: document auto reply random and cooldown"
```

---

## Self-Review

- 用户要求覆盖：@纯文本回复、@图片多模态直接进 LLM、@图片非多模态走视觉模型、无@ auto_reply=false 时纯图片忽略/文本和图文混合入上下文、无@ auto_reply=true 时按 random 回复、图片 RAG 占位。
- 随机回复：Task 3 将 random 和 cooldown 放到 handler 入图前，显式请求绕过，未命中仍由 detect_intent 按 `auto_reply=False` 入上下文。
- 类型一致性：`has_text` 在 `BotState`、handler、detect_intent、graph route 中统一使用 `bool`。
- 图片占位：`index_turn` 统一引用 `IMAGE_PLACEHOLDER`，不散落字面量。
