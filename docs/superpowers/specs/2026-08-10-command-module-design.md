# 指令模块设计

日期：2026-08-10
状态：已批准（2026-08-10）

## 背景

目标：为 bot 引入一套 QQ 聊天内的斜杠指令系统，先设计指令集，后续 CLI 模块复用同一套命令注册与执行接口。

现状核实：

- `common/prompts.py` 的 `ROUTER_PROMPT` 已把 `/` 开头视为“命令调用”，但项目没有命令注册表、解析器和分发器；当前所有文本消息都会进入 LangGraph。
- `MessageHandler` 是唯一消息入口，已有 `parse_content` 和按 thread 加锁的 worker，指令分发应复用这条链路。
- Satori 事件模型已经定义 `argv`（`interaction/command`），但目前 `main.py` 只注册 `message-created` 和 `login` 两类事件。
- 技能模块已建立“纯注册表 + 调用方注入依赖”的模式，指令模块沿用该风格，避免把 Satori 协议耦合进命令逻辑。

## 决策（用户已确认）

1. **形态 = 图外命令分发**：`MessageHandler` 在处理文本消息时先尝试解析并执行已注册指令；命中则直接回复，不进入 LangGraph，不经过 LLM/RAG/记忆工具。
2. **指令集 V1 = 只读 + 运维查询**：`/help`、`/ping`、`/version`、`/skills`、`/skill <name>`、`/status`。状态修改类指令（`/reset`、`/reload`、`/memory`、`/rag`）不进 V1。
3. **命令层与传输解耦**：定义 `Command`、`CommandRegistry`、`CommandContext`、`CommandResult`。Satori handler 发送 `result.text`；后续 CLI 直接打印 `result.text`。
4. **未注册命令保持现状**：只有命中已注册命令才拦截；`/未知指令` 继续交给 LLM，避免破坏现有 `/` 开头消息的对话行为。
5. **权限 = 静态管理员列表**：V1 用 `BOT_ADMIN_IDS` 判断聊天用户是否管理员；CLI 执行时构造 `is_admin=True` 的 actor。
6. **V1 不做动态加载**：命令由 Python 代码注册，不扫描目录、不热重载、不提供插件机制。

## 文件变更

| 文件 | 动作 | 说明 |
|---|---|---|
| `bot/core/commands/__init__.py` | 新增 | 导出 `CommandRegistry`、`Command`、`CommandContext`、`CommandResult`、`CommandActor`、`CommandServices` |
| `bot/core/commands/model.py` | 新增 | 命令模型与传输无关类型定义 |
| `bot/core/commands/parser.py` | 新增 | `/prefix name args...` 纯函数解析 |
| `bot/core/commands/registry.py` | 新增 | 命令注册、名称解析、帮助列表 |
| `bot/core/commands/builtin.py` | 新增 | V1 六个内置命令 handler |
| `bot/handler.py` | 改 | 文本消息进图前执行命令分发 |
| `common/config.py` | 改 | 加 `command_enabled` / `command_prefix` / `admin_ids` |
| `main.py` | 改 | 构建 `CommandServices` 和 `CommandRegistry`，注入 handler |
| `.env-template` | 改 | 加 `BOT_COMMAND_ENABLED` / `BOT_COMMAND_PREFIX` / `BOT_ADMIN_IDS` |
| `CLAUDE.md` | 改 | 新增指令模块架构、数据流、env 清单 |
| `tests/test_command_parser.py` | 新增 | 命令解析测试 |
| `tests/test_command_registry.py` | 新增 | 注册、解析、重复命令测试 |
| `tests/test_command_permissions.py` | 新增 | 权限判定测试 |
| `tests/test_command_handlers.py` | 新增 | 内置命令输出测试 |
| `tests/test_handler.py` | 改 | 指令命中时 graph 不被调用 |

## 配置（`BotConfig` 新增）

```python
command_enabled: bool   # BOT_COMMAND_ENABLED，默认 1
command_prefix: str     # BOT_COMMAND_PREFIX，默认 "/"
admin_ids: list[str]    # BOT_ADMIN_IDS，默认 []，逗号分隔
```

`command_enabled=0` 时指令分发完全关闭，所有消息走现有 LangGraph 流程。

## 命令模型

```python
@dataclass(frozen=True)
class Command:
    name: str
    description: str
    usage: str
    permission: str                 # "everyone" | "admin"
    handler: CommandHandler          # async def handler(ctx) -> CommandResult

@dataclass(frozen=True)
class CommandActor:
    user_id: str
    name: str
    is_admin: bool
    is_cli: bool = False

@dataclass(frozen=True)
class CommandContext:
    raw: str
    actor: CommandActor
    platform: str
    guild_id: str
    channel_id: str
    thread_id: str
    channel_type: int
    args: tuple[str, ...]
    config: BotConfig
    services: CommandServices

@dataclass(frozen=True)
class CommandResult:
    text: str
    data: dict | None = None          # 预留给 CLI 的 JSON/结构化输出
```

`CommandServices` 只注入 V1 需要的依赖：

```python
@dataclass
class CommandServices:
    version: str
    started_at: float
    bot_name: str
    skill_registry: SkillRegistry | None
    rag_service: RagService | None
    vision_service: VisionService | None
    memory_store: MemoryStore | None
    mcp_tool_count: int
```

## 命令解析

```text
prefix = config.command_prefix，默认 "/"
raw_text = parsed.clean_text

raw_text.startswith(prefix) 时才尝试解析：
  remainder = raw_text[len(prefix):].strip()
  name = 第一个空白分隔 token，小写化
  args = shlex.split(remainder[name_len:])
```

规则：

- 命令名只允许 `[a-z0-9_-]`，大小写不敏感。
- V1 只支持位置参数，不支持 `--option`。
- 参数用 `shlex.split` 解析，支持引号；解析失败按参数错误返回 usage。
- 未命中前缀或未注册命令时不拦截，继续进入 LangGraph。

## 指令集 V1

| 指令 | 权限 | 作用 | 参数 |
|---|---|---|---|
| `/help [command]` | 所有人 | 列出全部指令，或展示单个指令详情 | 可选：命令名 |
| `/ping` | 所有人 | 返回 `Pong.` | 无 |
| `/version` | 所有人 | 显示项目版本 | 无 |
| `/skills` | 所有人 | 列出已加载技能 | 无 |
| `/skill <name>` | 所有人 | 查看技能描述和正文 | 必选：技能名 |
| `/status` | 管理员 | 显示安全运行状态 | 无 |

输出约束：

- `/help` 无参数时按注册顺序列出 `usage - description`；带参数时显示 usage、description、permission。
- `/skills` 注册表为空时返回“当前没有可用技能。”；否则逐行输出 `- name: description`。
- `/skill <name>` 不存在时返回“技能不存在。”；正文输出截断到 2000 字符，避免刷屏。
- `/status` 输出版本、运行时间、LLM 模型名、数据库目录，以及 RAG/视觉/MCP/技能/记忆的启用状态。
- `/status` 绝不输出 API key、token、MCP URL、敏感配置。

## 数据流

```text
WebSocket event → MessageHandler._process
  → parse_content
  → 文本 + command_enabled + 命中 prefix + registry.resolve(name) 命中
    → 权限检查
    → Command.handler(ctx) → CommandResult
    → SatoriApiClient.send_message(result.text)
    → return（不进 LangGraph）
  → 其余消息
    → 现有 graph.ainvoke 流程
```

命令在 worker 的 thread 锁内执行，与普通消息保持同一 channel 串行化，为后续 `/reset` 等状态修改类命令预留正确边界。

`interaction/command` 事件不进入 V1 实现；接入时把 `argv.name` 和 `argv.arguments` 映射为同一个 `CommandContext`，复用同一 registry、权限和 handler。

## CLI 边界

CLI 模块后续按以下约束设计：

- CLI 构造 `CommandActor(user_id="<cli>", name="cli", is_admin=True, is_cli=True)`。
- CLI 构造 `CommandContext` 时使用 CLI 自己的 thread/channel 标识，不依赖 Satori 事件。
- CLI 直接打印 `CommandResult.text`；需要结构化输出时读取 `CommandResult.data`。
- CLI 自己的运维指令集单独设计，不把 bot 聊天指令全部搬进 CLI。

## 错误处理

| 场景 | 行为 |
|---|---|
| 指令未注册 | 不拦截，进入现有 LangGraph 流程 |
| 参数缺失或解析失败 | handler 返回 usage |
| 权限不足 | 返回“无权执行该指令。” |
| handler 异常 | 记录完整日志，回复“指令执行失败。”，不进入 LangGraph |
| `command_enabled=0` | 不解析、不执行任何指令 |
| 重复注册同名命令 | 启动时抛异常，快速失败 |

## 测试

| 测试 | 断言 |
|---|---|
| `test_command_parser.py` | prefix 命中、大小写、空白、引号参数、未命中 |
| `test_command_registry.py` | 注册/解析、重复命令失败、命令列表稳定 |
| `test_command_permissions.py` | everyone、admin、聊天非管理员、CLI 管理员 |
| `test_command_handlers.py` | `/help`、`/ping`、`/version`、`/skills`、`/skill`、`/status` 输出 |
| `test_handler.py`（扩展） | 指令命中时 fake graph 不被调用；普通消息仍走 graph |

## 风险

- 未注册 `/xxx` 仍会进入 LLM，可能出现“未知指令被模型回答”的情况；V1 接受该行为，后续如需严格模式可加 `BOT_COMMAND_STRICT`。
- `BOT_ADMIN_IDS` 是静态 ID 列表，不支持群角色、群主等动态权限；V1 够用，后续可在 actor 上扩展角色来源。
- `/status` 会暴露模型名、数据库目录、服务状态；这些属于运维信息，不包含密钥，但仍限制为管理员可见。

## 范围边界

- 不替换技能模块，也不把指令实现为 LLM 工具。
- 不实现 CLI 模块，只保证命令层接口可供 CLI 复用。
- 不注册 Satori 平台侧斜线指令，只解析聊天文本中的 `/` 指令。
