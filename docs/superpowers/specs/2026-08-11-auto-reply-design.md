# auto_reply 全局自动回复开关 — 设计文档

日期：2026-08-11
状态：已确认（方案 A）

## 目标

新增 `auto_reply` 参数，使 bot 在群聊中可对**非 @ 的文本/图片消息直接回复**（无需顶层 @）；管理员可经斜杠命令在运行时改写该参数。私聊恒回复、媒体（file/audio/video）永不回复为既有硬规则，不受影响。

## 决策记录

| 决策 | 结论 | 理由 |
|---|---|---|
| 回复范围 | 文本 + 图片全回 | 用户确认；图片回复触发视觉描述，成本随消息量上升，已接受 |
| 作用域 | 全局（所有群聊） | 状态存一处，命令不区分频道，实现最简 |
| 权限 | admin 专属（+ CLI 隐式 admin） | 改变回复行为影响面广，与 /status 同级 |
| 持久化 | 运行时态 | 命令改写值只存内存，重启回落到 `BOT_AUTO_REPLY` 环境变量默认 |
| 实现方案 | 方案 A：config 字段 + state 注入 + 命令改写 config | 最小改动；config 在 handler 恒存在，命令模块禁用时 env 值照样生效；判定表单一来源不破 |

## 架构

```
BOT_AUTO_REPLY (env, 启动默认)
        │ config.auto_reply (BotConfig, 非 frozen，命令可运行时改写)
        ▼
handler._process 图输入注入 state["auto_reply"]
        ▼
detect_intent 读 state → routing.decide_reply(..., auto_reply)
        ▼
群聊非@文本/图片 + auto_reply=True → should_respond=True → describe_image → call_llm
```

命令路径（图外）：`/auto_reply [on|off]`（admin）→ `_parse_flag` 校验 → `ctx.config.auto_reply = value` → 下一条消息处理时生效。

## 改动清单

### 1. `common/config.py`

Commands 段之后新增小节（`Flag` 严格布尔，fail-fast 语义与其它布尔一致）：

```python
# --- Reply behavior（回复行为；命令可运行时改写） ---
auto_reply: Flag = Field(
    default=False,
    validation_alias="BOT_AUTO_REPLY",
)
```

### 2. `object/bot/state.py`

`BotState` 的 detect_intent 字段区新增一行（纯新增，无 schema 删除，符合 LangGraph state 通道原子性约定）：

```python
    auto_reply: bool        # 群聊非@文本/图片是否直接回复（handler 从 config 注入，detect_intent 消费）
```

### 3. `bot/core/utils/routing.py`

`decide_reply` 增加 `auto_reply: bool = False` 参数（默认 False 保持既有行为，纯函数不破，兼容既有调用/测试）：

```python
def decide_reply(channel_type: int, content_kind: str, bot_id: str,
                 bot_name: str, mentions: dict, auto_reply: bool = False) -> bool:
    """媒体永不回复；私聊恒回复；群聊按顶层提及（id 主、昵称兜底）；
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

`keep_in_context` 与 `route_after_detect` **不改**——auto_reply 开启后群聊非@图片变回复轮，自动进上下文、走 describe_image→call_llm，行为由现有逻辑承接。

### 4. `bot/core/nodes/action_node/detect_intent.py`

```python
    auto_reply = state.get("auto_reply", False)
    should_respond = decide_reply(channel_type, content_kind, bot_id, bot_name, mentions, auto_reply)
```

### 5. `bot/handler.py`

`_process` 图输入新增一行（config 恒存在，命令禁用时 env 值照样生效）：

```python
    "auto_reply": self._bot_config.auto_reply if self._bot_config is not None else False,
```

### 6. `bot/core/commands/builtin.py`

新增 admin 命令 handler：

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

- 参数复用 `common.config._parse_flag`（0/1/true/false/yes/no/on/off 全兼容，布尔语义单一来源）；import `from common.config import _parse_flag`
- 注册 `Command(name="auto_reply", description="查看/设置全局自动回复开关", usage=f"{prefix}auto_reply [on|off]", permission="admin", handler=_auto_reply)`；命令名 `auto_reply` 匹配 `[a-z][a-z0-9_-]*`
- `/status` 加一行 `f"自动回复：{'开启' if cfg.auto_reply else '关闭'}"`
- `/help` 自动收录（registry.commands() 遍历）

## 错误处理

- 非法参数 → `_parse_flag` 抛 `ValueError`，命令内捕获回 usage，不触发 `run_command` 降级路径
- 权限不足 → `can_run` 现有 "无权执行该指令。"（handler._process 先查）
- `cfg.auto_reply = value`：BotConfig 非 frozen、无 `validate_assignment`，纯赋值不触发再校验，不会抛
- 命令执行异常 → `run_command` 现有 "指令执行失败。" 兜底

## 边界确认

- auto_reply=True + 群聊非@图片 → 触发视觉描述（Ollama），token 成本上升（已确认接受）
- auto_reply=True + file/audio/video → 仍永不回复（`NON_REPLY_KINDS` 上层拦截）
- auto_reply=True + 私聊 → 私聊本就恒回复，行为不变
- 命令模块禁用（`command_enabled=0`）→ auto_reply 仍从 env 生效（config 恒注入 handler）
- 重启 → 命令改写值丢失，回落到 `BOT_AUTO_REPLY` 环境变量默认

## 测试

- `tests/test_config.py`：`BOT_AUTO_REPLY` 解析（on/1/true→True，off/0/空→False，非法→ValidationError）
- `tests/test_routing.py`：auto_reply 判定四象限
  - 默认 False：群聊非@文本→False（既有行为回归）
  - True：群聊非@文本/图片→True
  - True + 媒体→False
  - DIRECT 恒 True（auto_reply 不影响）
- `tests/test_detect_intent.py`：state 注入 auto_reply=True → 群聊非@文本 should_respond=True
- `tests/test_command_handlers.py`：无参显示状态 / on|off 改写 config / 非法参数回 usage
- `tests/test_command_permissions.py`：非 admin 调 `/auto_reply` → "无权执行该指令。"
- `tests/test_handler.py`：图输入注入 `auto_reply` 字段

## 文档

- `.env-template`：加 `# BOT_AUTO_REPLY=0` + 注释（运行时 /auto_reply 可改写）
- `CLAUDE.md`：V1 命令清单加 `/auto_reply`；回复判定树 gotcha 补一句 auto_reply 覆盖范围

## 非目标

- 不做按频道/按群作用域
- 不做跨重启持久化
- 不改 `keep_in_context` / `route_after_detect` / 媒体硬规则
- 不加 rate-limit / 冷却逻辑（群聊高频消息的回复洪泛由使用方自行权衡）
