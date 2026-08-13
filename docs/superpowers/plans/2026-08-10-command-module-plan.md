# 指令模块实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 bot 引入图外斜杠指令系统，V1 提供 `/help`、`/ping`、`/version`、`/skills`、`/skill <name>`、`/status`，并让后续 CLI 复用同一命令层。

**Architecture:** 新增 `bot/core/commands/`，包含传输无关的命令模型、解析器、注册表和内置 handler；`MessageHandler` 在文本消息进入 LangGraph 前执行已注册指令。Satori 只负责构造 `CommandContext` 和发送 `CommandResult.text`，命令逻辑不依赖 Satori。

**Tech Stack:** Python 3.12、pydantic-settings、asyncio、shlex；无新增依赖。

## Global Constraints

- 项目命令：`uv run pytest`、`uv run ruff check`、`uv run python -c "..."`。
- 现有测试**必须全部保持通过**；`MessageHandler` 新增参数必须带默认值（`None`），不能改现有测试调用。
- 命令层不 import `bot.transport`、不依赖 Satori 事件对象；Satori 适配只出现在 `bot/handler.py`。
- 命令名只允许 `[a-z0-9_-]`，解析时小写化，`BOT_ADMIN_IDS` 为逗号分隔字符串。
- 只有命中已注册命令才拦截；未知 `/xxx` 继续进入现有 LangGraph 流程。
- `/status` 绝不输出 API key、token、MCP URL 等敏感配置。
- `BOT_COMMAND_PREFIX` 不能为空。
- 命令 handler 异常统一由 `run_command` 降级为“指令执行失败。”，不允许异常进入 LangGraph。
- 命令执行统一走 `uv run`（venv）。

---

### Task 1: BotConfig 命令配置

**Files:**
- Modify: `common/config.py`
- Modify: `.env-template`
- Modify: `tests/test_config.py`

**Interfaces:**
- Produces: `BotConfig.command_enabled: bool`、`BotConfig.command_prefix: str`、`BotConfig.admin_ids: list[str]`，环境变量为 `BOT_COMMAND_ENABLED`、`BOT_COMMAND_PREFIX`、`BOT_ADMIN_IDS`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_config.py` 的 `EXPECTED_DEFAULTS` 末尾追加：

```python
    "command_enabled": True,
    "command_prefix": "/",
    "admin_ids": [],
```

在 `ENV_SAMPLES` 末尾追加：

```python
    "command_enabled": ("0", False),
    "command_prefix": ("!", "!"),
    "admin_ids": ("u1, u2", ["u1", "u2"]),
```

追加空前缀校验测试：

```python
def test_empty_command_prefix_rejected(monkeypatch):
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("BOT_COMMAND_PREFIX", "")
    with pytest.raises(ValidationError):
        BotConfig(_env_file=None)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL，新字段不存在或 `.env-template` 缺 `BOT_COMMAND_ENABLED`。

- [ ] **Step 3: 实现配置字段**

在 `common/config.py` 的 skills 配置之后追加：

```python
    # --- Commands（图外斜杠指令模块） ---
    command_enabled: Flag = Field(
        default=True,
        validation_alias="BOT_COMMAND_ENABLED",
    )
    command_prefix: str = Field(
        default="/",
        min_length=1,
        validation_alias="BOT_COMMAND_PREFIX",
    )
    admin_ids: list[str] = Field(
        default_factory=list,
        validation_alias="BOT_ADMIN_IDS",
    )

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _parse_admin_ids(cls, value: object) -> list[str]:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        if isinstance(value, (list, tuple)):
            return [str(part).strip() for part in value if str(part).strip()]
        return []
```

在 `.env-template` 的 Skills 段后追加：

```text
# --- Commands ---
# BOT_COMMAND_ENABLED = 1
# BOT_COMMAND_PREFIX = /
# BOT_ADMIN_IDS = id1,id2
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add common/config.py .env-template tests/test_config.py
git commit -m "feat: add command module config"
```

---

### Task 2: 命令模型与解析器

**Files:**
- Create: `bot/core/commands/__init__.py`
- Create: `bot/core/commands/model.py`
- Create: `bot/core/commands/parser.py`
- Create: `tests/test_command_parser.py`

**Interfaces:**
- Consumes: `BotConfig`（仅类型注解，不读取 env）。
- Produces:
  - `bot/core/commands/model.py::CommandActor(user_id, name, is_admin, is_cli=False)`
  - `bot/core/commands/model.py::CommandServices(version, started_at, bot_name, skill_registry=None, rag_service=None, vision_service=None, memory_store=None, mcp_tool_count=0)`
  - `bot/core/commands/model.py::CommandContext(raw, actor, platform, guild_id, channel_id, thread_id, channel_type, args, config, services)`
  - `bot/core/commands/model.py::CommandResult(text, data=None)`
  - `bot/core/commands/model.py::Command(name, description, usage, permission, handler)`
  - `bot/core/commands/parser.py::ParsedCommand(name, args=(), error=None)`
  - `bot/core/commands/parser.py::parse_command(text, prefix="/") -> ParsedCommand | None`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_command_parser.py
"""命令解析测试：prefix、大小写、位置参数、引号与非法输入。"""

from bot.core.commands.parser import ParsedCommand, parse_command


def test_recognizes_default_prefix():
    assert parse_command("/ping", "/") == ParsedCommand(name="ping")


def test_command_name_is_lowercased():
    assert parse_command("/HELP status", "/") == ParsedCommand(
        name="help", args=("status",)
    )


def test_parses_quoted_arguments():
    assert parse_command('/skill "my skill"', "/") == ParsedCommand(
        name="skill", args=("my skill",)
    )


def test_ignores_message_without_prefix():
    assert parse_command("你好 /ping", "/") is None


def test_empty_command_returns_none():
    assert parse_command("/", "/") is None


def test_invalid_command_name_returns_none():
    assert parse_command("/Bad.Name", "/") is None


def test_unterminated_quote_reports_error():
    parsed = parse_command('/help "oops', "/")
    assert parsed is not None
    assert parsed.name == "help"
    assert parsed.error


def test_custom_prefix():
    assert parse_command("!ping", "!") == ParsedCommand(name="ping")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_command_parser.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'bot.core.commands'`。

- [ ] **Step 3: 实现模型与解析器**

```python
# bot/core/commands/model.py
"""命令模型：与 Satori / CLI 无关的核心类型。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from bot.core.memory import MemoryStore
    from bot.core.rag.service import RagService
    from bot.core.skills import SkillRegistry
    from bot.core.vision.service import VisionService
    from common import BotConfig

CommandHandler = Callable[["CommandContext"], Awaitable["CommandResult"]]


@dataclass(frozen=True)
class CommandActor:
    user_id: str
    name: str
    is_admin: bool
    is_cli: bool = False


@dataclass
class CommandServices:
    version: str
    started_at: float
    bot_name: str
    skill_registry: SkillRegistry | None = None
    rag_service: RagService | None = None
    vision_service: VisionService | None = None
    memory_store: MemoryStore | None = None
    mcp_tool_count: int = 0


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
    data: dict | None = None


@dataclass(frozen=True)
class Command:
    name: str
    description: str
    usage: str
    permission: str
    handler: CommandHandler
```

```python
# bot/core/commands/parser.py
"""斜杠指令纯解析器：prefix + 命令名 + 位置参数。"""

import re
import shlex
from dataclasses import dataclass

_NAME_RE = re.compile(r"[a-z0-9_-]+")


@dataclass(frozen=True)
class ParsedCommand:
    name: str
    args: tuple[str, ...] = ()
    error: str | None = None


def parse_command(text: str, prefix: str = "/") -> ParsedCommand | None:
    """解析 ``prefix + name + args``；未命中或不合法命令名返回 None。"""
    if not prefix or not text.startswith(prefix):
        return None
    remainder = text[len(prefix):].strip()
    if not remainder:
        return None
    name = remainder.split(None, 1)[0].lower()
    if not _NAME_RE.fullmatch(name):
        return None
    raw_args = remainder[len(name):].strip()
    if not raw_args:
        return ParsedCommand(name=name)
    try:
        args = tuple(shlex.split(raw_args))
    except ValueError as exc:
        return ParsedCommand(name=name, error=str(exc))
    return ParsedCommand(name=name, args=args)
```

```python
# bot/core/commands/__init__.py
"""指令模块：图外斜杠指令注册与分发。"""

from .model import Command, CommandActor, CommandContext, CommandResult, CommandServices
from .parser import ParsedCommand, parse_command

__all__ = [
    "Command",
    "CommandActor",
    "CommandContext",
    "CommandResult",
    "CommandServices",
    "ParsedCommand",
    "parse_command",
]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_command_parser.py -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add bot/core/commands tests/test_command_parser.py
git commit -m "feat: add command model and parser"
```

---

### Task 3: 命令注册表与权限执行器

**Files:**
- Create: `bot/core/commands/registry.py`
- Modify: `bot/core/commands/__init__.py`
- Create: `tests/test_command_registry.py`
- Create: `tests/test_command_permissions.py`

**Interfaces:**
- Consumes: Task 2 的 `Command`、`CommandActor`、`CommandContext`、`CommandResult`。
- Produces:
  - `bot/core/commands/registry.py::CommandRegistry.register(command) -> None`
  - `bot/core/commands/registry.py::CommandRegistry.resolve(name) -> Command | None`
  - `bot/core/commands/registry.py::CommandRegistry.commands() -> list[Command]`
  - `bot/core/commands/registry.py::can_run(command, actor) -> bool`
  - `bot/core/commands/registry.py::run_command(command, ctx) -> CommandResult`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_command_registry.py
"""CommandRegistry 注册、解析和稳定顺序。"""

import pytest

from bot.core.commands import Command, CommandRegistry, CommandResult


async def _ok(ctx):
    return CommandResult(text="ok")


def _cmd(name="ping", permission="everyone"):
    return Command(
        name=name,
        description="desc",
        usage=f"/{name}",
        permission=permission,
        handler=_ok,
    )


def test_register_and_resolve():
    reg = CommandRegistry()
    reg.register(_cmd("ping"))
    assert reg.resolve("PING").name == "ping"
    assert reg.resolve("missing") is None


def test_duplicate_name_raises():
    reg = CommandRegistry()
    reg.register(_cmd("ping"))
    with pytest.raises(ValueError):
        reg.register(_cmd("PING"))


def test_commands_preserve_registration_order():
    reg = CommandRegistry()
    reg.register(_cmd("ping"))
    reg.register(_cmd("help"))
    assert [c.name for c in reg.commands()] == ["ping", "help"]
```

```python
# tests/test_command_permissions.py
"""权限判定与 run_command 错误降级。"""

import asyncio

from bot.core.commands import (
    Command,
    CommandActor,
    CommandContext,
    CommandResult,
    CommandServices,
    run_command,
)
from common import BotConfig


async def _ok(ctx):
    return CommandResult(text="ok")


async def _boom(ctx):
    raise RuntimeError("boom")


def _ctx(actor):
    return CommandContext(
        raw="/status",
        actor=actor,
        platform="test",
        guild_id="",
        channel_id="ch1",
        thread_id="t1",
        channel_type=1,
        args=(),
        config=BotConfig(_env_file=None),
        services=CommandServices(version="test", started_at=0.0, bot_name=""),
    )


def _command(handler, permission="everyone"):
    return Command(
        name="status",
        description="desc",
        usage="/status",
        permission=permission,
        handler=handler,
    )


def test_everyone_command_allows_non_admin():
    cmd = _command(_ok)
    result = asyncio.run(run_command(cmd, _ctx(CommandActor("u1", "n", False))))
    assert result.text == "ok"


def test_admin_command_denies_non_admin():
    cmd = _command(_ok, permission="admin")
    result = asyncio.run(run_command(cmd, _ctx(CommandActor("u1", "n", False))))
    assert result.text == "无权执行该指令。"


def test_admin_command_allows_admin():
    cmd = _command(_ok, permission="admin")
    result = asyncio.run(run_command(cmd, _ctx(CommandActor("admin", "a", True))))
    assert result.text == "ok"


def test_cli_actor_is_admin():
    cmd = _command(_ok, permission="admin")
    actor = CommandActor("<cli>", "cli", True, is_cli=True)
    result = asyncio.run(run_command(cmd, _ctx(actor)))
    assert result.text == "ok"


def test_handler_exception_returns_failure():
    cmd = _command(_boom)
    result = asyncio.run(run_command(cmd, _ctx(CommandActor("u1", "n", False))))
    assert result.text == "指令执行失败。"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_command_registry.py tests/test_command_permissions.py -v`
Expected: FAIL，`CommandRegistry` / `run_command` 不存在。

- [ ] **Step 3: 实现注册表与权限执行器**

```python
# bot/core/commands/registry.py
"""命令注册表、权限检查和统一执行入口。"""

import logging

from .model import Command, CommandActor, CommandContext, CommandResult

logger = logging.getLogger(__name__)


class CommandRegistry:
    """内存命令注册表，按注册顺序保存。"""

    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}

    def register(self, command: Command) -> None:
        key = command.name.lower()
        if key in self._commands:
            raise ValueError(f"duplicate command: {command.name}")
        self._commands[key] = command

    def resolve(self, name: str) -> Command | None:
        return self._commands.get(name.lower())

    def commands(self) -> list[Command]:
        return list(self._commands.values())


def can_run(command: Command, actor: CommandActor) -> bool:
    """管理员命令只允许 admin actor；everyone 命令对所有人开放。"""
    return command.permission != "admin" or actor.is_admin


async def run_command(command: Command, ctx: CommandContext) -> CommandResult:
    """统一执行：先查权限，再降级 handler 异常。"""
    if not can_run(command, ctx.actor):
        return CommandResult(text="无权执行该指令。")
    try:
        return await command.handler(ctx)
    except Exception:
        logger.exception("Command %s failed for actor %s", command.name, ctx.actor.user_id)
        return CommandResult(text="指令执行失败。")
```

更新 `bot/core/commands/__init__.py`：

```python
"""指令模块：图外斜杠指令注册与分发。"""

from .model import Command, CommandActor, CommandContext, CommandResult, CommandServices
from .parser import ParsedCommand, parse_command
from .registry import CommandRegistry, can_run, run_command

__all__ = [
    "Command",
    "CommandActor",
    "CommandContext",
    "CommandRegistry",
    "CommandResult",
    "CommandServices",
    "ParsedCommand",
    "can_run",
    "parse_command",
    "run_command",
]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_command_registry.py tests/test_command_permissions.py -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add bot/core/commands tests/test_command_registry.py tests/test_command_permissions.py
git commit -m "feat: add command registry and permission runner"
```

---

### Task 4: 内置命令 handler

**Files:**
- Modify: `bot/core/skills/loader.py`
- Modify: `bot/core/commands/__init__.py`
- Create: `bot/core/commands/builtin.py`
- Modify: `tests/test_skill_loader.py`
- Create: `tests/test_command_handlers.py`

**Interfaces:**
- Consumes: Task 1 的 `BotConfig`、Task 2 的模型、Task 3 的 `CommandRegistry`，以及 `SkillRegistry`。
- Produces:
  - `bot/core/skills/loader.py::SkillRegistry.get_skill(name) -> Skill | None`
  - `bot/core/commands/builtin.py::build_command_registry(services, prefix="/") -> CommandRegistry`

- [ ] **Step 1: 写失败测试**

在 `tests/test_skill_loader.py` 末尾追加：

```python
def test_get_skill_returns_skill_or_none():
    skill = Skill(name="x", description="d", body="b")
    reg = SkillRegistry({"x": skill})
    assert reg.get_skill("x") == skill
    assert reg.get_skill("ghost") is None
```

```python
# tests/test_command_handlers.py
"""V1 内置命令 handler 输出测试。"""

import asyncio
import time

from bot.core.commands import (
    CommandActor,
    CommandContext,
    CommandServices,
    build_command_registry,
)
from bot.core.skills import Skill, SkillRegistry
from common import BotConfig


def _services(skills=None):
    return CommandServices(
        version="1.2.3",
        started_at=time.time() - 65,
        bot_name="test-bot",
        skill_registry=skills,
        mcp_tool_count=2,
    )


def _ctx(services, args=(), actor=None):
    return CommandContext(
        raw="/" + " ".join(args),
        actor=actor or CommandActor(user_id="u1", name="tester", is_admin=False),
        platform="test",
        guild_id="",
        channel_id="ch1",
        thread_id="t1",
        channel_type=1,
        args=args,
        config=BotConfig(_env_file=None, admin_ids=["admin1"]),
        services=services,
    )


def _execute(registry, services, name, args=(), actor=None):
    command = registry.resolve(name)
    ctx = _ctx(services, args=args, actor=actor)
    return asyncio.run(command.handler(ctx))


def test_help_lists_all_commands():
    services = _services()
    registry = build_command_registry(services)
    result = _execute(registry, services, "help")
    assert "/help" in result.text
    assert "/ping" in result.text


def test_help_shows_single_command():
    services = _services()
    registry = build_command_registry(services)
    result = _execute(registry, services, "help", ("status",))
    assert "/status" in result.text
    assert "管理员" in result.text


def test_ping():
    services = _services()
    registry = build_command_registry(services)
    result = _execute(registry, services, "ping")
    assert result.text == "Pong."


def test_version():
    services = _services()
    registry = build_command_registry(services)
    result = _execute(registry, services, "version")
    assert result.text == "qq-bot 1.2.3"


def test_skills_empty():
    services = _services()
    registry = build_command_registry(services)
    result = _execute(registry, services, "skills")
    assert result.text == "当前没有可用技能。"


def test_skills_lists_index():
    skills = SkillRegistry({"x": Skill(name="x", description="描述", body="正文")})
    services = _services(skills)
    registry = build_command_registry(services)
    result = _execute(registry, services, "skills")
    assert "- x: 描述" in result.text


def test_skill_returns_description_and_body():
    skills = SkillRegistry({"x": Skill(name="x", description="描述", body="正文内容")})
    services = _services(skills)
    registry = build_command_registry(services)
    result = _execute(registry, services, "skill", ("x",))
    assert "描述" in result.text
    assert "正文内容" in result.text


def test_skill_missing():
    services = _services(SkillRegistry({}))
    registry = build_command_registry(services)
    result = _execute(registry, services, "skill", ("missing",))
    assert result.text == "技能不存在。"


def test_skill_requires_arg():
    services = _services()
    registry = build_command_registry(services)
    result = _execute(registry, services, "skill")
    assert "用法" in result.text


def test_status_returns_safe_runtime_info():
    services = _services()
    registry = build_command_registry(services)
    result = _execute(registry, services, "status")
    assert "qq-bot 1.2.3" in result.text
    assert "sensenova-6.7-flash-lite" in result.text
    assert "db" in result.text
    assert "MCP：2 个工具" in result.text
    assert "API_KEY" not in result.text
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_skill_loader.py tests/test_command_handlers.py -v`
Expected: FAIL，`get_skill` / `build_command_registry` 不存在。

- [ ] **Step 3: 实现 SkillRegistry.get_skill 与内置命令**

在 `bot/core/skills/loader.py` 的 `has` 方法后追加：

```python
    def get_skill(self, name: str) -> Skill | None:
        """返回指定技能对象；不存在返回 None。"""
        return self._skills.get(name)
```

```python
# bot/core/commands/builtin.py
"""V1 内置命令：help / ping / version / skills / skill / status。"""

import time
from functools import partial

from .model import Command, CommandContext, CommandResult, CommandServices
from .registry import CommandRegistry


async def _help(ctx: CommandContext, registry: CommandRegistry) -> CommandResult:
    if not ctx.args:
        lines = ["可用指令："]
        lines.extend(f"{cmd.usage} - {cmd.description}" for cmd in registry.commands())
        return CommandResult(text="\n".join(lines))
    command = registry.resolve(ctx.args[0])
    if command is None:
        return CommandResult(text=f"指令不存在：{ctx.args[0]}")
    permission = "管理员" if command.permission == "admin" else command.permission
    return CommandResult(
        text=f"{command.usage}\n{command.description}\n权限：{permission}"
    )


async def _ping(ctx: CommandContext) -> CommandResult:
    return CommandResult(text="Pong.")


async def _version(ctx: CommandContext) -> CommandResult:
    return CommandResult(text=f"qq-bot {ctx.services.version}")


async def _skills(ctx: CommandContext) -> CommandResult:
    registry = ctx.services.skill_registry
    if registry is None or registry.total == 0:
        return CommandResult(text="当前没有可用技能。")
    return CommandResult(text="\n".join(registry.index_lines()))


async def _skill(ctx: CommandContext) -> CommandResult:
    if len(ctx.args) != 1:
        return CommandResult(text="用法：/skill <name>")
    registry = ctx.services.skill_registry
    if registry is None:
        return CommandResult(text="技能功能未启用。")
    skill = registry.get_skill(ctx.args[0].lower())
    if skill is None:
        return CommandResult(text=f"技能不存在：{ctx.args[0]}")
    return CommandResult(text=f"{skill.description}\n\n{skill.body[:2000]}")


def _format_uptime(seconds: float) -> str:
    secs = max(0, int(seconds))
    hours, rem = divmod(secs, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours}小时{minutes}分{secs}秒"


async def _status(ctx: CommandContext) -> CommandResult:
    cfg = ctx.config
    services = ctx.services
    lines = [
        f"qq-bot {services.version}",
        f"运行时间：{_format_uptime(time.time() - services.started_at)}",
        f"LLM：{cfg.llm_model}",
        f"数据库目录：{cfg.db_dir}",
        f"RAG：{'开启' if services.rag_service is not None else '关闭'}",
        f"视觉：{'开启' if services.vision_service is not None else '关闭'}",
        f"MCP：{services.mcp_tool_count} 个工具",
        f"技能：{services.skill_registry.total if services.skill_registry else 0} 个",
        f"记忆：{'开启' if services.memory_store is not None else '关闭'}",
    ]
    return CommandResult(text="\n".join(lines))


def build_command_registry(services: CommandServices, prefix: str = "/") -> CommandRegistry:
    """按固定顺序注册 V1 内置命令。"""
    registry = CommandRegistry()
    registry.register(Command(
        name="help",
        description="查看指令帮助",
        usage=f"{prefix}help [command]",
        permission="everyone",
        handler=partial(_help, registry=registry),
    ))
    registry.register(Command(
        name="ping",
        description="检查 bot 是否在线",
        usage=f"{prefix}ping",
        permission="everyone",
        handler=_ping,
    ))
    registry.register(Command(
        name="version",
        description="显示项目版本",
        usage=f"{prefix}version",
        permission="everyone",
        handler=_version,
    ))
    registry.register(Command(
        name="skills",
        description="列出已加载技能",
        usage=f"{prefix}skills",
        permission="everyone",
        handler=_skills,
    ))
    registry.register(Command(
        name="skill",
        description="查看指定技能",
        usage=f"{prefix}skill <name>",
        permission="everyone",
        handler=_skill,
    ))
    registry.register(Command(
        name="status",
        description="显示安全运行状态",
        usage=f"{prefix}status",
        permission="admin",
        handler=_status,
    ))
    return registry
```

更新 `bot/core/commands/__init__.py`：

```python
"""指令模块：图外斜杠指令注册与分发。"""

from .builtin import build_command_registry
from .model import Command, CommandActor, CommandContext, CommandResult, CommandServices
from .parser import ParsedCommand, parse_command
from .registry import CommandRegistry, can_run, run_command

__all__ = [
    "Command",
    "CommandActor",
    "CommandContext",
    "CommandRegistry",
    "CommandResult",
    "CommandServices",
    "ParsedCommand",
    "build_command_registry",
    "can_run",
    "parse_command",
    "run_command",
]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_skill_loader.py tests/test_command_handlers.py -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add bot/core/skills/loader.py bot/core/commands tests/test_skill_loader.py tests/test_command_handlers.py
git commit -m "feat: add builtin slash commands"
```

---

### Task 5: MessageHandler 图外分发

**Files:**
- Modify: `bot/handler.py`
- Modify: `tests/test_handler.py`

**Interfaces:**
- Consumes: Task 3/4 的 `CommandRegistry`、`CommandServices`、`parse_command`、`run_command`。
- Produces: `MessageHandler(command_registry=None, command_services=None)` 新构造参数；指令命中时直接回复且不调用 graph。

- [ ] **Step 1: 写失败测试**

更新 `tests/test_handler.py` 顶部 import：

```python
from bot.core.commands import CommandServices, build_command_registry
from common import BotConfig
```

把 `_StubApi` 改成可记录发送内容：

```python
class _StubApi:
    def __init__(self):
        self.sent = []

    async def send_message(self, channel_id, content):
        self.sent.append((channel_id, content))
```

把 `_make_handler` 改为支持命令注入：

```python
def _make_handler(graph, bot_config=None, command_registry=None, command_services=None):
    return MessageHandler(
        client=object(),
        graph=graph,
        persona="你是{bot_name}",
        api_client=_StubApi(),
        bot_config=bot_config,
        command_registry=command_registry,
        command_services=command_services,
    )
```

在文件末尾追加测试：

```python
def _command_event(content):
    return EventBody(
        id=2,
        sn=2,
        type="message-created",
        platform="llonebot",
        channel=Channel(id="ch1", type=ChannelType.DIRECT),
        user=User(id="admin1", name="admin"),
        message=Message(id="m2", content=content),
    )


def _command_services():
    return CommandServices(version="test", started_at=0.0, bot_name="")


def test_registered_command_skips_graph():
    graph = _StubGraph()
    services = _command_services()
    registry = build_command_registry(services)
    config = BotConfig(_env_file=None, command_enabled=True, admin_ids=["admin1"])
    handler = _make_handler(
        graph,
        bot_config=config,
        command_registry=registry,
        command_services=services,
    )

    asyncio.run(handler._process({
        "event": _command_event("/ping"),
        "platform": "llonebot",
        "guild_id": "",
        "channel_id": "ch1",
        "user_id": "admin1",
        "thread_id": "llonebot::private:ch1",
    }))

    assert graph.state is None
    assert handler._api_client.sent == [("ch1", "Pong.")]


def test_unknown_command_still_enters_graph():
    graph = _StubGraph()
    services = _command_services()
    registry = build_command_registry(services)
    config = BotConfig(_env_file=None, command_enabled=True, admin_ids=["admin1"])
    handler = _make_handler(
        graph,
        bot_config=config,
        command_registry=registry,
        command_services=services,
    )

    asyncio.run(handler._process({
        "event": _command_event("/unknown"),
        "platform": "llonebot",
        "guild_id": "",
        "channel_id": "ch1",
        "user_id": "admin1",
        "thread_id": "llonebot::private:ch1",
    }))

    assert graph.state is not None


def test_admin_command_permission_denied_skips_graph():
    graph = _StubGraph()
    services = _command_services()
    registry = build_command_registry(services)
    config = BotConfig(_env_file=None, command_enabled=True, admin_ids=["admin1"])
    handler = _make_handler(
        graph,
        bot_config=config,
        command_registry=registry,
        command_services=services,
    )

    asyncio.run(handler._process({
        "event": _command_event("/status"),
        "platform": "llonebot",
        "guild_id": "",
        "channel_id": "ch1",
        "user_id": "u-not-admin",
        "thread_id": "llonebot::private:ch1",
    }))

    assert graph.state is None
    assert handler._api_client.sent[0][1] == "无权执行该指令。"


def test_command_disabled_enters_graph():
    graph = _StubGraph()
    services = _command_services()
    registry = build_command_registry(services)
    config = BotConfig(_env_file=None, command_enabled=False)
    handler = _make_handler(
        graph,
        bot_config=config,
        command_registry=registry,
        command_services=services,
    )

    asyncio.run(handler._process({
        "event": _command_event("/ping"),
        "platform": "llonebot",
        "guild_id": "",
        "channel_id": "ch1",
        "user_id": "admin1",
        "thread_id": "llonebot::private:ch1",
    }))

    assert graph.state is not None


def test_malformed_command_args_returns_usage():
    graph = _StubGraph()
    services = _command_services()
    registry = build_command_registry(services)
    config = BotConfig(_env_file=None, command_enabled=True, admin_ids=["admin1"])
    handler = _make_handler(
        graph,
        bot_config=config,
        command_registry=registry,
        command_services=services,
    )

    asyncio.run(handler._process({
        "event": _command_event('/help "oops'),
        "platform": "llonebot",
        "guild_id": "",
        "channel_id": "ch1",
        "user_id": "admin1",
        "thread_id": "llonebot::private:ch1",
    }))

    assert graph.state is None
    assert handler._api_client.sent[0][1] == "指令参数错误，用法：/help [command]"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_handler.py -v`
Expected: FAIL，`MessageHandler` 不接受 `command_registry` 参数。

- [ ] **Step 3: 实现 MessageHandler 分发**

在 `bot/handler.py` 顶部 import 区域追加：

```python
from bot.core.commands import (
    CommandActor,
    CommandContext,
    CommandRegistry,
    CommandServices,
    parse_command,
    run_command,
)
```

修改构造函数：

```python
    def __init__(
        self,
        client: SatoriClient,
        graph: CompiledGraph,
        persona: str,
        api_client: SatoriApiClient,
        bot_config=None,
        command_registry: CommandRegistry | None = None,
        command_services: CommandServices | None = None,
    ) -> None:
        self.client = client
        self.graph = graph
        self._persona = persona
        self._api_client = api_client
        self._bot_config = bot_config
        self._command_registry = command_registry
        self._command_services = command_services
```

在 `_process` 中 `parsed = parse_content(raw_content)` 之后、`logger.info` 之前插入：

```python
        if (
            self._command_registry is not None
            and self._command_services is not None
            and self._bot_config is not None
            and self._bot_config.command_enabled
            and content_kind == "text"
        ):
            parsed_cmd = parse_command(
                parsed.clean_text, self._bot_config.command_prefix
            )
            if parsed_cmd is not None:
                command = self._command_registry.resolve(parsed_cmd.name)
                if command is not None:
                    actor = CommandActor(
                        user_id=user_id,
                        name=user_name,
                        is_admin=user_id in (self._bot_config.admin_ids or []),
                    )
                    ctx = CommandContext(
                        raw=raw_content,
                        actor=actor,
                        platform=item["platform"],
                        guild_id=item["guild_id"],
                        channel_id=channel_id,
                        thread_id=thread_id,
                        channel_type=channel_type,
                        args=parsed_cmd.args,
                        config=self._bot_config,
                        services=self._command_services,
                    )
                    if parsed_cmd.error:
                        reply_text = f"指令参数错误，用法：{command.usage}"
                    else:
                        reply_text = (await run_command(command, ctx)).text
                    if reply_text:
                        await self._send_reply(channel_id, reply_text)
                    return
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_handler.py -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add bot/handler.py tests/test_handler.py
git commit -m "feat: dispatch slash commands before graph"
```

---

### Task 6: main.py 接线与文档

**Files:**
- Modify: `main.py`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: Task 4 的 `CommandServices`、`build_command_registry`。
- Produces: bot 启动时构建 command registry，并通过 `MessageHandler` 启用指令分发。

- [ ] **Step 1: 修改 main.py**

顶部 import 追加：

```python
import time
from importlib.metadata import PackageNotFoundError, version

from bot.core.commands import CommandServices, build_command_registry
```

在 `main()` 之前加版本 helper：

```python
def _bot_version() -> str:
    try:
        return version("qq-bot")
    except PackageNotFoundError:
        return "0.1.0"
```

在 `main()` 的 `logger.info("Starting QQ bot ...")` 后加：

```python
    started_at = time.time()
```

在 `skill_registry` 初始化之后、`create_graph` 调用之前加：

```python
    command_services = CommandServices(
        version=_bot_version(),
        started_at=started_at,
        bot_name="",
        skill_registry=skill_registry,
        rag_service=rag_service,
        vision_service=vision_service,
        memory_store=memory_store,
        mcp_tool_count=len(mcp_tools),
    )
    command_registry = (
        build_command_registry(command_services, config.command_prefix)
        if config.command_enabled
        else None
    )
```

把 `MessageHandler` 构造改为：

```python
    handler = MessageHandler(
        client, graph, persona, api_client,
        bot_config=config,
        command_registry=command_registry,
        command_services=command_services,
    )
```

- [ ] **Step 2: 修改 CLAUDE.md**

在 Architecture 的 `skills/` 之后追加：

```text
    commands/                 #  图外斜杠指令：模型、解析器、注册表、内置命令
      model.py                #   CommandActor / CommandServices / CommandContext / CommandResult / Command
      parser.py               #   parse_command — prefix + 命令名 + shlex 位置参数
      registry.py             #   CommandRegistry / can_run / run_command（权限 + 异常降级）
      builtin.py              #   build_command_registry — /help /ping /version /skills /skill /status
```

在 Data flow 的 `WebSocket event` 行后追加：

```text
    → 已注册斜杠指令命中：权限检查 → Command.handler → send reply → 不进 LangGraph
```

在 env 清单中追加：

```text
# --- Commands ---
BOT_COMMAND_ENABLED = 1
BOT_COMMAND_PREFIX = /
BOT_ADMIN_IDS =
```

- [ ] **Step 3: 运行完整测试与 lint**

Run: `uv run pytest`
Expected: PASS。

Run: `uv run ruff check`
Expected: PASS。

- [ ] **Step 4: Commit**

```bash
git add main.py CLAUDE.md
git commit -m "feat: wire command module into bot entrypoint"
```
