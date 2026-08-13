# auto_reply 全局自动回复开关 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `auto_reply` 参数，使 bot 在群聊中可对**非 @ 的文本/图片**直接回复，并可由管理员经 `/auto_reply` 命令在运行时改写。

**Architecture:** `BOT_AUTO_REPLY`（env 启动默认）→ `config.auto_reply`（BotConfig 字段，命令可运行时改写）→ handler 注入图 state → `detect_intent` 读 state 传给 `routing.decide_reply` → 群聊非@文本/图片在 auto_reply=True 时回复。回复判定仍在唯一判定表 `routing.py`，媒体永不回复硬规则不受影响。

**Tech Stack:** pydantic-settings（Flag 严格布尔）、LangGraph state 注入、Satori 斜杠指令模块、pytest。

## Global Constraints

- 布尔语义单一来源：`common.config._parse_flag`（`1/0/true/false/yes/no/on/off/空串`；非法抛 `ValueError`）
- `decide_reply` 新增参数必须带默认值 `auto_reply: bool = False`——既有调用/测试零改动
- 媒体（file/audio/video）永不回复：auto_reply **不**覆盖 `NON_REPLY_KINDS`
- 权限：`/auto_reply` 为 admin（`BOT_ADMIN_IDS` 或 CLI 隐式 admin）；非 admin → `can_run` 返回 "无权执行该指令。"
- 作用域全局、运行时态：命令改写只存内存，重启回落到 `BOT_AUTO_REPLY`；**不做文件/DB 持久化、不做按频道**
- 命令名 `auto_reply` 匹配 `[a-z][a-z0-9_-]*`；参数经 shlex POSIX 分词
- `config.py` 字段声明必须位于 `@field_validator` 装饰器之前（pydantic 字段不能定义在方法后）
- `.env-template` 必须含 `BOT_AUTO_REPLY`（`test_env_template_contains_all_env_aliases` 会校验）
- 测试命令用 `uv run pytest`；lint 用 `uv run ruff check`
- 每次改动后跑**全量** `test_config.py`——`EXPECTED_DEFAULTS`/`ENV_SAMPLES` 两个字典与配置字段强一致，漏改即红

---

### Task 1: config 字段 + 测试字典 + .env-template

**Files:**
- Modify: `common/config.py`（admin_ids 字段后、validator 前）
- Modify: `tests/test_config.py:13-57`（EXPECTED_DEFAULTS）、`:60-107`（ENV_SAMPLES）
- Modify: `.env-template`

**Interfaces:**
- Produces: `BotConfig.auto_reply: bool`（`Flag`，env `BOT_AUTO_REPLY`，默认 `False`）—— Task 5 的 `/auto_reply` 命令读写它；Task 4 的 handler 读它

- [ ] **Step 1: 更新 test_config.py 两个字典**

在 `EXPECTED_DEFAULTS` 末尾（`"admin_ids": []` 之后）加：
```python
    "auto_reply": False,
```
在 `ENV_SAMPLES` 末尾（`"admin_ids": ...` 之后）加：
```python
    "auto_reply": ("1", True),
```
在 `test_invalid_bool_rejected` 附近新增：
```python
def test_invalid_auto_reply_rejected(monkeypatch):
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("BOT_AUTO_REPLY", "2")
    with pytest.raises(ValidationError):
        BotConfig(_env_file=None)
```

- [ ] **Step 2: 运行 test_config 确认红**

Run: `uv run pytest tests/test_config.py -q`
Expected: FAIL——`test_defaults_without_env_file`/`test_every_field_has_env_alias` 报 `auto_reply` 缺失（`EXPECTED_DEFAULTS` 有但 config 无此字段）。

- [ ] **Step 3: config.py 加字段**

在 `common/config.py` 的 `admin_ids` 字段块（第 244 行 `)`, 之后、`@field_validator("admin_ids"...)` 装饰器之前）插入：
```python

    # --- Reply behavior（回复行为；命令可运行时改写） ---
    auto_reply: Flag = Field(
        default=False,
        validation_alias="BOT_AUTO_REPLY",
    )
```

- [ ] **Step 4: .env-template 加注释行**

在 `# --- Commands ---` 段之后加：
```
# --- Reply behavior ---
# BOT_AUTO_REPLY = 0   # 群聊非@文本/图片自动回复（管理员可经 /auto_reply 运行时改写）
```

- [ ] **Step 5: 运行 test_config 确认绿**

Run: `uv run pytest tests/test_config.py -q`
Expected: PASS（含 `test_env_template_contains_all_env_aliases`、`test_invalid_auto_reply_rejected`）。

- [ ] **Step 6: Commit**

```bash
git add common/config.py tests/test_config.py .env-template
git commit -m "feat: 新增 auto_reply 配置项（BOT_AUTO_REPLY，Flag 严格布尔）"
```

---

### Task 2: routing.decide_reply 支持 auto_reply

**Files:**
- Modify: `bot/core/utils/routing.py:19-26`（decide_reply）
- Test: `tests/test_routing.py`

**Interfaces:**
- Consumes: 无
- Produces: `decide_reply(channel_type, content_kind, bot_id, bot_name, mentions, auto_reply=False) -> bool` —— Task 3 的 detect_intent 传入该参数

- [ ] **Step 1: 写失败测试**

在 `tests/test_routing.py` 的 `# --- decide_reply ---` 区末尾追加：
```python
# --- decide_reply with auto_reply ---

def test_auto_reply_group_text_replies():
    assert decide_reply(ChannelType.TEXT, "text", "bot1", "Bot", {}, auto_reply=True) is True


def test_auto_reply_group_image_replies():
    assert decide_reply(ChannelType.TEXT, "image", "bot1", "Bot", {}, auto_reply=True) is True


def test_auto_reply_media_still_never_replies():
    for kind in ("file", "audio", "video"):
        assert decide_reply(ChannelType.TEXT, kind, "bot1", "Bot", {}, auto_reply=True) is False


def test_auto_reply_off_is_default_no_change():
    # 不带 auto_reply 参数 → 既有行为（群聊非@不回复）
    assert decide_reply(ChannelType.TEXT, "text", "bot1", "Bot", {}) is False


def test_auto_reply_direct_unchanged():
    assert decide_reply(ChannelType.DIRECT, "text", "bot1", "Bot", {}, auto_reply=True) is True
```

- [ ] **Step 2: 运行确认红**

Run: `uv run pytest tests/test_routing.py -q`
Expected: FAIL——`TypeError: decide_reply() got an unexpected keyword argument 'auto_reply'`。

- [ ] **Step 3: 实现**

将 `bot/core/utils/routing.py` 的 `decide_reply` 替换为：
```python
def decide_reply(channel_type: int, content_kind: str, bot_id: str,
                 bot_name: str, mentions: dict, auto_reply: bool = False) -> bool:
    """should_respond：媒体永不回复；私聊回复；群聊按顶层提及判定（id 为主、昵称兜底）；
    auto_reply=True 时群聊非@文本/图片也回复（媒体仍排除）。"""
    if content_kind in NON_REPLY_KINDS:
        return False
    if channel_type == ChannelType.DIRECT:
        return True
    if auto_reply:
        return True
    mentioned_names = set(mentions.values())
    return bool(bot_id in mentions or (bot_name and bot_name in mentioned_names))
```

- [ ] **Step 4: 运行确认绿**

Run: `uv run pytest tests/test_routing.py -q`
Expected: PASS（旧用例全部保持 + 新 5 个用例）。

- [ ] **Step 5: Commit**

```bash
git add bot/core/utils/routing.py tests/test_routing.py
git commit -m "feat: decide_reply 支持 auto_reply 参数（默认 False 保持既有行为）"
```

---

### Task 3: state 字段 + detect_intent 消费

**Files:**
- Modify: `object/bot/state.py:32-35`（detect_intent 字段区）
- Modify: `bot/core/nodes/action_node/detect_intent.py:31-39`
- Test: `tests/test_detect_intent.py`

**Interfaces:**
- Consumes: `decide_reply(..., auto_reply=False)`（Task 2 签名）
- Produces: BotState 新增 `auto_reply: bool` 字段；detect_intent 读 `state.get("auto_reply", False)` —— Task 4 的 handler 注入该 state 字段

- [ ] **Step 1: 写失败测试**

在 `tests/test_detect_intent.py` 的判定树区末尾追加：
```python
def test_group_non_at_text_responds_when_auto_reply():
    state = make_state(
        llm_text="晚上吃什么",
        content_kind="text",
        channel_type=0,
        bot_id="bot1",
        auto_reply=True,
    )
    result = asyncio.run(detect_intent(state))
    assert result["should_respond"] is True
    assert len(result["messages"]) == 1  # 回复轮入上下文


def test_group_non_at_image_responds_when_auto_reply():
    state = make_state(
        content_kind="image",
        channel_type=0,
        bot_id="bot1",
        auto_reply=True,
    )
    result = asyncio.run(detect_intent(state))
    assert result["should_respond"] is True
    assert len(result["messages"]) == 1


def test_auto_reply_absent_defaults_to_off():
    # make_state 不含 auto_reply → .get 兜底 False，既有行为不变
    state = make_state(
        llm_text="晚上吃什么",
        channel_type=0,
        bot_id="bot1",
    )
    result = asyncio.run(detect_intent(state))
    assert result["should_respond"] is False
```

- [ ] **Step 2: 运行确认红**

Run: `uv run pytest tests/test_detect_intent.py -q`
Expected: FAIL——前两个用例 `should_respond is False`。

- [ ] **Step 3: 实现 state.py + detect_intent.py**

`object/bot/state.py` 的 detect_intent 字段区（`user_name` 行之后）加：
```python
    auto_reply: bool        # 群聊非@文本/图片是否直接回复（handler 从 config 注入，detect_intent 消费）
```

`bot/core/nodes/action_node/detect_intent.py` 的 `mentions = state.get("mentions", {})` 之后加：
```python
    auto_reply = state.get("auto_reply", False)
```
并将 `should_respond` 计算行改为：
```python
    should_respond = decide_reply(channel_type, content_kind, bot_id, bot_name, mentions, auto_reply)
```

- [ ] **Step 4: 运行确认绿**

Run: `uv run pytest tests/test_detect_intent.py tests/test_routing.py -q`
Expected: PASS（新 3 用例 + Task 2 用例；旧 detect_intent 用例不变）。

- [ ] **Step 5: Commit**

```bash
git add object/bot/state.py bot/core/nodes/action_node/detect_intent.py tests/test_detect_intent.py
git commit -m "feat: detect_intent 消费 auto_reply，群聊非@文本/图片可自动回复"
```

---

### Task 4: handler 注入 auto_reply 到图 state

**Files:**
- Modify: `bot/handler.py:236`（图输入 dict，`image_srcs` 之后）
- Test: `tests/test_handler.py`

**Interfaces:**
- Consumes: BotState `auto_reply` 字段（Task 3 定义）；`BotConfig.auto_reply`（Task 1）
- Produces: 图输入恒含 `auto_reply: bool` 键

- [ ] **Step 1: 写失败测试**

在 `tests/test_handler.py` 末尾追加：
```python
def test_auto_reply_injected_into_graph_state():
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
    assert graph.state["auto_reply"] is True


def test_auto_reply_defaults_false_when_config_absent():
    graph = _StubGraph()
    handler = _make_handler(graph)  # bot_config=None
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

- [ ] **Step 2: 运行确认红**

Run: `uv run pytest tests/test_handler.py -q`
Expected: FAIL——`KeyError: 'auto_reply'`（图 state 无此键）。

- [ ] **Step 3: 实现**

在 `bot/handler.py` 图输入 dict 的 `"image_srcs": image_srcs,` 之后加：
```python
                    "auto_reply": self._bot_config.auto_reply if self._bot_config is not None else False,
```

- [ ] **Step 4: 运行确认绿**

Run: `uv run pytest tests/test_handler.py -q`
Expected: PASS（新 2 用例 + 既有 command dispatch 用例不变）。

- [ ] **Step 5: Commit**

```bash
git add bot/handler.py tests/test_handler.py
git commit -m "feat: handler 注入 auto_reply 到图 state（config 恒存在，命令禁用也生效）"
```

---

### Task 5: /auto_reply 命令 + /status 显示

**Files:**
- Modify: `bot/core/commands/builtin.py`
- Test: `tests/test_command_handlers.py`、`tests/test_command_permissions.py`

**Interfaces:**
- Consumes: `BotConfig.auto_reply`（Task 1）；`common.config._parse_flag`；`CommandContext.config`/`args`
- Produces: 注册命令 `auto_reply`（permission="admin"）；`_auto_reply(ctx) -> CommandResult`

- [ ] **Step 1: 写失败测试**

在 `tests/test_command_handlers.py` 末尾追加：
```python
def test_auto_reply_shows_state():
    services = _services()
    registry = build_command_registry(services)
    result = _execute(registry, services, "auto_reply")
    assert "当前状态：关闭" in result.text


def test_auto_reply_turns_on():
    services = _services()
    registry = build_command_registry(services)
    ctx = _ctx(services, args=("on",), actor=CommandActor(user_id="admin1", name="admin", is_admin=True))
    result = asyncio.run(registry.resolve("auto_reply").handler(ctx))
    assert result.text == "auto_reply 已开启。"
    assert ctx.config.auto_reply is True


def test_auto_reply_turns_off():
    services = _services()
    registry = build_command_registry(services)
    ctx = _ctx(services, args=("off",))
    result = asyncio.run(registry.resolve("auto_reply").handler(ctx))
    assert result.text == "auto_reply 已关闭。"
    assert ctx.config.auto_reply is False


def test_auto_reply_invalid_arg_returns_usage():
    services = _services()
    registry = build_command_registry(services)
    result = _execute(registry, services, "auto_reply", ("maybe",))
    assert "参数无效" in result.text


def test_auto_reply_is_admin_command():
    services = _services()
    registry = build_command_registry(services)
    assert registry.resolve("auto_reply").permission == "admin"
    assert "/auto_reply" in _execute(registry, services, "help").text  # /help 自动收录
```

在 `tests/test_command_permissions.py` 追加（需在 imports 加 `build_command_registry`）：
```python
def test_auto_reply_registered_admin_denies_non_admin():
    services = CommandServices(version="test", started_at=0.0, bot_name="")
    registry = build_command_registry(services)
    command = registry.resolve("auto_reply")
    result = asyncio.run(run_command(command, _ctx(CommandActor("u1", "n", False))))
    assert result.text == "无权执行该指令。"
```

- [ ] **Step 2: 运行确认红**

Run: `uv run pytest tests/test_command_handlers.py tests/test_command_permissions.py -q`
Expected: FAIL——`AttributeError: 'NoneType' object has no attribute 'handler'`（resolve 返回 None）。

- [ ] **Step 3: 实现 builtin.py**

顶部 imports 加：
```python
from common.config import _parse_flag
```
在 `_status` 之后、`build_command_registry` 之前加：
```python
async def _auto_reply(ctx: CommandContext) -> CommandResult:
    cfg = ctx.config
    if not ctx.args:
        return CommandResult(text=f"auto_reply 当前状态：{'开启' if cfg.auto_reply else '关闭'}")
    if len(ctx.args) != 1:
        return CommandResult(text="用法：/auto_reply [on|off]")
    try:
        value = _parse_flag(ctx.args[0])
    except ValueError:
        return CommandResult(text="参数无效，用法：/auto_reply [on|off]")
    cfg.auto_reply = value
    return CommandResult(text=f"auto_reply 已{'开启' if value else '关闭'}。")
```

`_status` 的 `lines` 列表末尾（`记忆：` 之后）加：
```python
        f"自动回复：{'开启' if cfg.auto_reply else '关闭'}",
```

`build_command_registry` 中 `status` 注册块之后追加：
```python
    registry.register(Command(
        name="auto_reply",
        description="查看/设置全局自动回复开关",
        usage=f"{prefix}auto_reply [on|off]",
        permission="admin",
        handler=_auto_reply,
    ))
```

- [ ] **Step 4: 运行确认绿**

Run: `uv run pytest tests/test_command_handlers.py tests/test_command_permissions.py -q`
Expected: PASS（新 6 用例 + 既有用例不变）。

- [ ] **Step 5: Commit**

```bash
git add bot/core/commands/builtin.py tests/test_command_handlers.py tests/test_command_permissions.py
git commit -m "feat: 新增 /auto_reply 命令（admin），/status 显示自动回复状态"
```

---

### Task 6: CLAUDE.md 文档 + 全量验证

**Files:**
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: 前 5 个任务的落地产物

- [ ] **Step 1: 更新 CLAUDE.md 两处**

① 指令模块 key pattern 中 V1 命令清单：
```
V1：`/help /ping /version /skills /skill /status`（status 为 admin）
```
改为：
```
V1：`/help /ping /version /skills /skill /status /auto_reply`（status/auto_reply 为 admin，auto_reply 运行时改写 BOT_AUTO_REPLY）
```

② 回复判定树 gotcha 末尾（`图文混合按主类型` 之后）追加：
```
`BOT_AUTO_REPLY=1` 或管理员 `/auto_reply on` 后，群聊非@文本/图片也回复（媒体仍永不回复；全局、运行时态，重启回落 env 默认）
```

- [ ] **Step 2: 全量测试**

Run: `uv run pytest -q`
Expected: 全绿（348 + 新增约 16 个用例）。若有红，先修再提交。

- [ ] **Step 3: Lint**

Run: `uv run ruff check common/config.py object/bot/state.py bot/core/utils/routing.py bot/core/nodes/action_node/detect_intent.py bot/handler.py bot/core/commands/builtin.py`
Expected: "All checks passed!"（无 BLE001/DTZ——沿用既有 ignore 策略）。

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: CLAUDE.md 记录 /auto_reply 命令与 auto_reply 回复判定覆盖"
```

---

## Self-Review

**Spec coverage:**
- config 字段（spec §1）→ Task 1 ✓
- state 字段（spec §2）→ Task 3 ✓
- decide_reply + keep_in_context 不改（spec §3）→ Task 2 只改 decide_reply ✓
- detect_intent 消费（spec §4）→ Task 3 ✓
- handler 注入（spec §5）→ Task 4 ✓
- /auto_reply 命令 + status + help 收录（spec §6）→ Task 5 ✓
- 错误处理（非法参数→usage / 权限→拒绝 / 赋值不抛）→ Task 5 用例覆盖 ✓
- 测试矩阵（config/routing/detect_intent/command_handlers/permissions/handler）→ Task 1-5 各含 ✓
- 文档（.env-template + CLAUDE.md）→ Task 1/6 ✓
- 非目标（不持久化、不按频道、不改媒体规则）→ 计划无越界步骤 ✓

**Placeholder scan:** 无 TBD/TODO；每步含完整代码或确切插入点。

**Type consistency:**
- `decide_reply(..., auto_reply: bool = False)` 在 Task 2 定义，Task 3 detect_intent 按此签名传参 ✓
- `BotConfig.auto_reply` Task 1 定义，Task 4 handler / Task 5 命令读、Task 5 命令写 ✓
- `_parse_flag` 来自 `common.config`，Task 5 引入 ✓
- 命令名 `auto_reply` 一致贯穿测试与注册 ✓
