# Skill 模块实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 bot 引入提示词包技能系统——`skills/<name>/SKILL.md`（frontmatter + 正文），LLM 经索引发现、`load_skill` 按需加载、跨轮会话内持续生效。

**Architecture:** 新增 `bot/core/skills/`（SkillRegistry 启动扫描 + load/unload 纯函数）与 `skill_manager` action_node（把激活技能写入 `BotState.active_skills`）。`build_system_messages`/`estimate_context_tokens` 加 `skill_registry`/`active_skills` 参数注入技能索引层 + 激活正文层（与既有「实际注入/估算单一来源」硬约束一致）。图边改为 `tools → skill_manager → call_llm`。

**Tech Stack:** Python 3.12、langgraph 1.2.2、langchain_core、无新增依赖（frontmatter 手写最小解析器，不引 pyyaml）。

## Global Constraints

- 项目命令：`uv run pytest`、`uv run ruff check`、`uv run python -c "..."`。
- 现有测试**必须全部保持通过**——所有新增函数参数必须有默认值（`skill_registry=None`），不改任何现有调用签名。
- `active_skills` 字段**绝不由 handler/输入 state 注入**（输入会覆盖 checkpoint，导致跨轮失效）；节点一律 `state.get("active_skills", [])` 兜底。
- `build_system_messages` 与 `estimate_context_tokens` 必须走同一构造（token 估算与实际注入永不偏离，CLAUDE.md 硬性约束）。
- 技能名只允许 `[a-z0-9_-]`（`re.fullmatch(r"[a-z0-9_-]+", name)`）。
- frontmatter 只解析 `name`/`description` 两个字段，`key: value` 行式，不引入 YAML 依赖。
- 降级哲学：目录缺失/单技能损坏/正文读取失败一律跳过 + warning，绝不崩 bot。
- 命令执行统一走 `uv run`（venv）。

---

### Task 1: SkillRegistry 加载器

**Files:**
- Create: `bot/core/skills/__init__.py`
- Create: `bot/core/skills/loader.py`
- Test: `tests/test_skill_loader.py`

**Interfaces:**
- Produces:
  - `bot/core/skills/loader.py::Skill` dataclass：`Skill(name: str, description: str, body: str)`
  - `bot/core/skills/loader.py::SkillRegistry`：
    - `__init__(self, skills: dict[str, Skill] | None = None, index_max: int = 50)` — 内存构造（测试直接用它，无需 stub）
    - `@classmethod from_directory(cls, skills_dir: str, index_max: int = 50) -> SkillRegistry` — 扫描 `skills/<name>/SKILL.md`
    - `total: int` property、`names() -> list[str]`、`has(name) -> bool`、`get_body(name) -> str | None`、`index_lines() -> list[str]`（`- name: description`）、`index_text() -> str`（截断 + 「…共 N 个技能」脚注）
  - `bot/core/skills/__init__.py` 导出：`Skill`, `SkillRegistry`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_skill_loader.py
"""SkillRegistry 加载器测试：frontmatter 解析、非法跳过、索引截断。"""

from bot.core.skills import Skill, SkillRegistry


def _write_skill(tmp_path, name, md_text):
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(md_text, encoding="utf-8")
    return d


def test_parses_frontmatter_and_body(tmp_path):
    _write_skill(tmp_path, "translate", (
        "---\n"
        "name: translate\n"
        "description: 中英互译\n"
        "---\n"
        "\n"
        "## 规则\n"
        "保留语气"
    ))
    reg = SkillRegistry.from_directory(str(tmp_path))
    assert reg.names() == ["translate"]
    assert reg.total == 1
    assert reg.has("translate")
    assert "保留语气" in reg.get_body("translate")


def test_missing_directory_returns_empty(tmp_path):
    reg = SkillRegistry.from_directory(str(tmp_path / "nope"))
    assert reg.total == 0
    assert reg.index_text() == ""


def test_skips_skill_without_frontmatter(tmp_path):
    _write_skill(tmp_path, "bad", "## 没有 frontmatter 的正文")
    reg = SkillRegistry.from_directory(str(tmp_path))
    assert reg.names() == []


def test_skips_skill_missing_name_or_description(tmp_path):
    _write_skill(tmp_path, "a", "---\ndescription: 缺 name\n---\n正文")
    _write_skill(tmp_path, "b", "---\nname: b\n---\n正文")
    reg = SkillRegistry.from_directory(str(tmp_path))
    assert reg.names() == []


def test_skips_invalid_name(tmp_path):
    _write_skill(tmp_path, "Bad Name", "---\nname: Bad Name\ndescription: 含空格\n---\n正文")
    reg = SkillRegistry.from_directory(str(tmp_path))
    assert reg.names() == []


def test_duplicate_name_last_wins(tmp_path):
    _write_skill(tmp_path, "a", "---\nname: dup\ndescription: 第一版\n---\nbody1")
    _write_skill(tmp_path, "b", "---\nname: dup\ndescription: 第二版\n---\nbody2")
    reg = SkillRegistry.from_directory(str(tmp_path))
    assert reg.names() == ["dup"]
    assert reg.get_body("dup") == "body2"


def test_index_text_truncates_and_notes_total(tmp_path):
    for i in range(5):
        _write_skill(tmp_path, f"s{i}", f"---\nname: s{i}\ndescription: d{i}\n---\nb")
    reg = SkillRegistry.from_directory(str(tmp_path), index_max=3)
    text = reg.index_text()
    assert "- s0: d0" in text
    assert "…共 5 个技能，仅显示前 3 个" in text


def test_index_text_all_when_under_max(tmp_path):
    for i in range(2):
        _write_skill(tmp_path, f"s{i}", f"---\nname: s{i}\ndescription: d{i}\n---\nb")
    reg = SkillRegistry.from_directory(str(tmp_path), index_max=3)
    assert "…共" not in reg.index_text()


def test_in_memory_construction_for_tests():
    reg = SkillRegistry({"x": Skill(name="x", description="d", body="b")}, index_max=5)
    assert reg.names() == ["x"]
    assert reg.has("x")
    assert reg.get_body("x") == "b"
    assert reg.get_body("ghost") is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_skill_loader.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'bot.core.skills'`）

- [ ] **Step 3: 最小实现**

```python
# bot/core/skills/__init__.py
"""技能模块：SKILL.md 提示词包（SkillRegistry 加载 + load/unload 工具）。"""

from .loader import Skill, SkillRegistry

__all__ = ["Skill", "SkillRegistry"]
```

```python
# bot/core/skills/loader.py
"""SkillRegistry — 扫描 skills/<name>/SKILL.md，解析 frontmatter 构建技能索引。

frontmatter 最小解析（不引 pyyaml）：``---`` 包住的 ``key: value`` 行。
只读 name/description 两个字段；缺失/非法一律跳过 + warning，绝不崩 bot。
"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_NAME_RE = re.compile(r"[a-z0-9_-]+")
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass
class Skill:
    name: str
    description: str
    body: str


def _parse_skill_md(path: Path) -> tuple[str, str, str] | None:
    """解析 SKILL.md → (name, description, body)；非法返回 None。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        logger.warning("skill file unreadable: %s", path)
        return None
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    name = meta.get("name", "").strip()
    description = meta.get("description", "").strip()
    if not name or not description:
        return None
    body = text[m.end():].strip()
    if not body:
        return None
    return name, description, body


class SkillRegistry:
    """技能注册表：内存 {name: Skill} + 目录扫描加载。"""

    def __init__(self, skills: dict[str, Skill] | None = None, index_max: int = 50) -> None:
        self._skills: dict[str, Skill] = skills or {}
        self.index_max = index_max

    @classmethod
    def from_directory(cls, skills_dir: str, index_max: int = 50) -> "SkillRegistry":
        """扫描 ``skills/<name>/SKILL.md`` 构建注册表；目录不存在 → 空注册表。"""
        skills: dict[str, Skill] = {}
        base = Path(skills_dir)
        if not base.is_dir():
            return cls(skills, index_max)
        for skill_dir in sorted(base.iterdir()):
            if not skill_dir.is_dir():
                continue
            md = skill_dir / "SKILL.md"
            if not md.is_file():
                continue
            parsed = _parse_skill_md(md)
            if parsed is None:
                logger.warning("skill %s: SKILL.md 缺少合法 frontmatter，跳过", skill_dir.name)
                continue
            name, description, body = parsed
            if not _NAME_RE.fullmatch(name):
                logger.warning("skill %s: name %r 非法（须 [a-z0-9_-]），跳过", skill_dir.name, name)
                continue
            skills[name] = Skill(name=name, description=description, body=body)  # 重复取最后一个

        return cls(skills, index_max)

    @property
    def total(self) -> int:
        return len(self._skills)

    def names(self) -> list[str]:
        return list(self._skills)

    def has(self, name: str) -> bool:
        return name in self._skills

    def get_body(self, name: str) -> str | None:
        skill = self._skills.get(name)
        return skill.body if skill is not None else None

    def index_lines(self) -> list[str]:
        """LLM 可见的索引行（每技能一行，排序稳定——dict 保持插入序）。"""
        return [f"- {s.name}: {s.description}" for s in self._skills.values()]

    def index_text(self) -> str:
        """完整索引文本，超过 index_max 截断并附「共 N 个」脚注。"""
        lines = self.index_lines()
        shown = lines[: self.index_max]
        text = "\n".join(shown)
        if len(lines) > self.index_max:
            text += f"\n…共 {len(lines)} 个技能，仅显示前 {self.index_max} 个"
        return text
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_skill_loader.py -v`
Expected: PASS（10 passed）

- [ ] **Step 5: Commit**

```bash
git add bot/core/skills tests/test_skill_loader.py
git commit -m "feat: SkillRegistry 加载器（skills/SKILL.md frontmatter 解析 + 索引截断）"
```

---

### Task 2: 配置字段 + 提示词常量

**Files:**
- Modify: `common/config.py`（`BotConfig` 加 3 字段）
- Modify: `common/prompts.py`（加 2 常量）
- Modify: `common/__init__.py`（导出新常量）
- Modify: `.env-template`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces:
  - `BotConfig.skills_enabled: bool`（`BOT_SKILLS_ENABLED`，默认 `"1"`）
  - `BotConfig.skills_dir: str`（`BOT_SKILLS_DIR`，默认 `"skills"`）
  - `BotConfig.skills_index_max: int`（`BOT_SKILLS_INDEX_MAX`，默认 `50`）
  - `common.SKILL_INDEX_HINT`（默认 `"可用技能（按需用 load_skill 加载正文）："`）
  - `common.SKILL_ACTIVE_HINT`（默认 `"当前已激活技能（遵循其规则）："`）
- Consumes: 无（Task 1 的注册表与本任务无关）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_config.py 末尾追加
def test_skills_enabled_by_default(monkeypatch):
    monkeypatch.delenv("BOT_SKILLS_ENABLED", raising=False)
    assert BotConfig().skills_enabled is True


def test_skills_enabled_false_values(monkeypatch):
    for v in ("0", "false", "False", ""):
        monkeypatch.setenv("BOT_SKILLS_ENABLED", v)
        assert BotConfig().skills_enabled is False


def test_skills_dir_and_index_max_defaults(monkeypatch):
    monkeypatch.delenv("BOT_SKILLS_DIR", raising=False)
    monkeypatch.delenv("BOT_SKILLS_INDEX_MAX", raising=False)
    config = BotConfig()
    assert config.skills_dir == "skills"
    assert config.skills_index_max == 50


def test_skills_dir_and_index_max_env(monkeypatch):
    monkeypatch.setenv("BOT_SKILLS_DIR", "my-skills")
    monkeypatch.setenv("BOT_SKILLS_INDEX_MAX", "120")
    config = BotConfig()
    assert config.skills_dir == "my-skills"
    assert config.skills_index_max == 120
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL（`AttributeError: 'BotConfig' object has no attribute 'skills_enabled'`）

- [ ] **Step 3: 最小实现**

`common/config.py` 在 `tavily_api_key` 字段后追加（保持 dataclass 分组风格）：

```python
    # --- Skills（提示词包技能，按需加载正文） ---
    skills_enabled: bool = field(
        default_factory=lambda: os.getenv("BOT_SKILLS_ENABLED", "1") not in ("0", "false", "False", ""),
    )
    skills_dir: str = field(
        default_factory=lambda: os.getenv("BOT_SKILLS_DIR", "skills"),
    )
    skills_index_max: int = field(
        default_factory=lambda: int(os.getenv("BOT_SKILLS_INDEX_MAX", "50")),
    )
```

`common/prompts.py` 末尾追加：

```python
# 技能模块提示词（call_llm 经 build_system_messages 动态注入）
SKILL_INDEX_HINT = "可用技能（按需用 load_skill 加载正文）："
SKILL_ACTIVE_HINT = "当前已激活技能（遵循其规则）："
```

`common/__init__.py`：把两个常量加入 import 与 `__all__`。

`.env-template` 在 RAG 段后追加：

```
# --- Skills 提示词包技能 (默认启用) ---
# BOT_SKILLS_ENABLED = 1
# BOT_SKILLS_DIR = skills
# BOT_SKILLS_INDEX_MAX = 50
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS（原 3 个 + 新 4 个）

- [ ] **Step 5: Commit**

```bash
git add common/config.py common/prompts.py common/__init__.py .env-template tests/test_config.py
git commit -m "feat: skill 配置字段 + SKILL_INDEX/ACTIVE_HINT 提示词 + env-template"
```

---

### Task 3: load_skill / unload_skill 纯函数

**Files:**
- Create: `bot/core/skills/tools.py`
- Modify: `bot/core/skills/__init__.py`（导出工具）
- Test: `tests/test_skill_tools.py`

**Interfaces:**
- Consumes: Task 1 的 `SkillRegistry`（`has`/`get_body`/`names`）
- Produces:
  - `bot/core/skills/tools.py::load_skill(skill_name: str, skill_registry) -> str`（async）
  - `bot/core/skills/tools.py::unload_skill(skill_name: str) -> str`（async，幂等）
  - `bot.core.skills` 导出 `load_skill`, `unload_skill`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_skill_tools.py
"""load_skill / unload_skill 纯函数测试。"""

import asyncio

from bot.core.skills import Skill, SkillRegistry, load_skill, unload_skill


def _registry():
    return SkillRegistry({
        "translate": Skill(name="translate", description="中英互译", body="## 规则\n保留语气"),
    })


def test_load_skill_returns_body():
    out = asyncio.run(load_skill("translate", _registry()))
    assert "保留语气" in out
    assert "translate" in out


def test_load_skill_unknown_name_lists_available():
    out = asyncio.run(load_skill("ghost", _registry()))
    assert "ghost" in out
    assert "translate" in out


def test_load_skill_no_registry_returns_disabled():
    out = asyncio.run(load_skill("translate", None))
    assert "未启用" in out


def test_unload_skill_idempotent_confirmation():
    out1 = asyncio.run(unload_skill("translate"))
    out2 = asyncio.run(unload_skill("translate"))
    assert out1 == out2
    assert "translate" in out1
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_skill_tools.py -v`
Expected: FAIL（`ImportError: cannot import name 'load_skill'`）

- [ ] **Step 3: 最小实现**

```python
# bot/core/skills/tools.py
"""load_skill / unload_skill 纯函数。

只返回正文/确认文本，不写任何状态——激活状态的写回由 skill_manager 节点
从 state 完成（工具经 ToolNode 返回 ToolMessage 后由节点消费）。
"""

async def load_skill(skill_name: str, skill_registry) -> str:
    """返回技能正文；不存在/未启用给出可纠正提示。"""
    if skill_registry is None:
        return "技能功能未启用。"
    body = skill_registry.get_body(skill_name)
    if body is None:
        available = ", ".join(skill_registry.names())
        return f"技能 '{skill_name}' 不存在。可用技能：{available or '（无）'}"
    return f"已加载技能 '{skill_name}'，正文：\n{body}"


async def unload_skill(skill_name: str) -> str:
    """返回停用确认（幂等）。"""
    return f"技能 '{skill_name}' 已停用。"
```

`bot/core/skills/__init__.py` 更新导出：

```python
from .loader import Skill, SkillRegistry
from .tools import load_skill, unload_skill

__all__ = ["Skill", "SkillRegistry", "load_skill", "unload_skill"]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_skill_tools.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: Commit**

```bash
git add bot/core/skills tests/test_skill_tools.py
git commit -m "feat: load_skill/unload_skill 纯函数（不写状态，写回在 skill_manager 节点）"
```

---

### Task 4: build_system_messages 技能注入层

**Files:**
- Modify: `bot/core/utils/context.py`
- Test: `tests/test_context.py`

**Interfaces:**
- Consumes: Task 1 `SkillRegistry`（`index_text()`/`get_body()`/`total`）、Task 2 `SKILL_INDEX_HINT`/`SKILL_ACTIVE_HINT`
- Produces（签名变更，全部带默认值，现有调用不受影响）:
  - `build_system_messages(persona, summary="", now=None, skill_registry=None, active_skills=None) -> list[SystemMessage]`
  - `estimate_context_tokens(messages, persona, summary, skill_registry=None, active_skills=None) -> int`
- 注入顺序：persona → 时间 → 摘要 → **技能索引** → **已激活技能正文**（两条独立 SystemMessage）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_context.py 末尾追加
"""技能层：索引 + 激活正文注入；估算与实际注入一致。"""

from bot.core.skills import Skill, SkillRegistry


def test_skill_index_and_active_layers_injected():
    registry = SkillRegistry({
        "translate": Skill(name="translate", description="中英互译", body="## 规则\n保留语气"),
        "weather": Skill(name="weather", description="播报天气", body="## 规则\n查天气"),
    })
    msgs = build_system_messages(
        "你是助手", "摘要", now=FIXED_NOW,
        skill_registry=registry, active_skills=["translate"],
    )
    assert [m.content for m in msgs] == [
        "你是助手", TIME_HINT, "之前的对话摘要：\n摘要",
        "可用技能（按需用 load_skill 加载正文）：\n- translate: 中英互译\n- weather: 播报天气",
        "当前已激活技能（遵循其规则）：\n\n===== 技能：translate =====\n## 规则\n保留语气",
    ]


def test_skill_layers_skipped_when_no_registry():
    msgs = build_system_messages("你是助手", "摘要", now=FIXED_NOW)
    assert [m.content for m in msgs] == ["你是助手", TIME_HINT, "之前的对话摘要：\n摘要"]


def test_skill_index_truncated_when_exceeds_max():
    registry = SkillRegistry(
        {f"s{i}": Skill(name=f"s{i}", description=f"d{i}", body="b") for i in range(5)},
        index_max=3,
    )
    msgs = build_system_messages("你是助手", "", now=FIXED_NOW, skill_registry=registry)
    index_msg = [m for m in msgs if "可用技能" in m.content]
    assert index_msg and "…共 5 个技能，仅显示前 3 个" in index_msg[0].content


def test_active_skill_missing_body_skipped_keeps_others():
    registry = SkillRegistry({"a": Skill(name="a", description="d", body="正文A")})
    msgs = build_system_messages(
        "你是助手", "", now=FIXED_NOW,
        skill_registry=registry, active_skills=["a", "ghost"],
    )
    active_msg = [m for m in msgs if "已激活技能" in m.content]
    assert len(active_msg) == 1
    assert "ghost" not in active_msg[0].content
    assert "正文A" in active_msg[0].content


def test_no_active_layer_when_empty_active_skills():
    registry = SkillRegistry({"a": Skill(name="a", description="d", body="正文A")})
    msgs = build_system_messages("你是助手", "", now=FIXED_NOW, skill_registry=registry, active_skills=[])
    assert not any("已激活技能" in m.content for m in msgs)


def test_estimate_includes_skill_layers():
    from langchain_core.messages import HumanMessage
    from langchain_core.messages.utils import count_tokens_approximately

    from bot.core.utils import estimate_context_tokens

    registry = SkillRegistry({"translate": Skill(name="translate", description="中英互译", body="## 规则")})
    msgs = [HumanMessage(content="你好")]
    expected = build_system_messages(
        "你是助手", "摘要", now=FIXED_NOW, skill_registry=registry, active_skills=["translate"],
    ) + msgs
    assert estimate_context_tokens(
        msgs, "你是助手", "摘要", skill_registry=registry, active_skills=["translate"],
    ) == count_tokens_approximately(expected, chars_per_token=1.5)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_context.py -v`
Expected: FAIL（现有 `test_estimate_builds_same_layers_as_builder` 应仍通过；新技能层测试因未实现注入而失败——`build_system_messages` 忽略新参数）

- [ ] **Step 3: 最小实现**

`bot/core/utils/context.py`：

```python
from common import CURRENT_TIME_HINT, SKILL_ACTIVE_HINT, SKILL_INDEX_HINT
```

在 `_current_time_hint` 后新增两个构造助手：

```python
def _skill_index_message(skill_registry) -> SystemMessage | None:
    """技能索引层：无注册表或空 → None。"""
    if skill_registry is None or skill_registry.total == 0:
        return None
    return SystemMessage(content=f"{SKILL_INDEX_HINT}\n{skill_registry.index_text()}")


def _active_skills_message(skill_registry, active_skills: list[str]) -> SystemMessage | None:
    """已激活技能正文层：无激活或全部读取失败 → None。"""
    if not active_skills or skill_registry is None:
        return None
    sections: list[str] = []
    for name in active_skills:
        body = skill_registry.get_body(name)
        if body is None:
            continue  # 技能已激活但文件被删 → 静默跳过
        sections.append(f"===== 技能：{name} =====\n{body}")
    if not sections:
        return None
    return SystemMessage(content=f"{SKILL_ACTIVE_HINT}\n\n" + "\n\n".join(sections))
```

`build_system_messages` 签名与注入（末尾追加）：

```python
def build_system_messages(
    persona: str,
    summary: str = "",
    now: datetime.datetime | None = None,
    skill_registry=None,
    active_skills: list[str] | None = None,
) -> list[SystemMessage]:
    ...
    msgs = [SystemMessage(content=persona)] if persona.strip() else []
    msgs.append(_current_time_hint(now))
    if summary_text.strip():
        msgs.append(SystemMessage(content=f"之前的对话摘要：\n{summary_text}"))
    index_msg = _skill_index_message(skill_registry)
    if index_msg is not None:
        msgs.append(index_msg)
    active_msg = _active_skills_message(skill_registry, active_skills or [])
    if active_msg is not None:
        msgs.append(active_msg)
    return msgs
```

`estimate_context_tokens` 签名与透传：

```python
def estimate_context_tokens(
    messages: list[BaseMessage],
    persona: str,
    summary: str,
    skill_registry=None,
    active_skills: list[str] | None = None,
) -> int:
    all_msgs = build_system_messages(
        persona, summary, skill_registry=skill_registry, active_skills=active_skills,
    )
    all_msgs.extend(messages)
    return count_tokens_approximately(all_msgs, chars_per_token=_CHARS_PER_TOKEN)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_context.py -v`
Expected: PASS（原 6 个 + 新 6 个）

- [ ] **Step 5: Commit**

```bash
git add bot/core/utils/context.py tests/test_context.py
git commit -m "feat: build_system_messages 注入技能索引+激活正文层，估算同步一致"
```

---

### Task 5: skill_manager 节点

**Files:**
- Create: `bot/core/nodes/action_node/skill_manager.py`
- Modify: `bot/core/nodes/action_node/__init__.py`
- Modify: `bot/core/nodes/__init__.py`
- Test: `tests/test_skill_manager.py`

**Interfaces:**
- Consumes: Task 1 `SkillRegistry`（`has`）、`BotState.messages`
- Produces:
  - `skill_manager_node(state: BotState, skill_registry=None) -> dict` — 返回 `{"active_skills": [...]}`（变更时）或 `{}`（no-op）
  - `bot.core.nodes.skill_manager_node` 导出
- 关键行为：从 `state["messages"]` **从后往前**找最后一个带 `tool_calls` 的 AIMessage；`load_skill` 需 `registry.has(name)` 校验；`unload_skill` 幂等；无技能调用返回 `{}`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_skill_manager.py
"""skill_manager 节点：从 AIMessage tool_calls 更新 active_skills。"""

import asyncio

from langchain_core.messages import AIMessage, ToolMessage

from bot.core.nodes.action_node.skill_manager import skill_manager_node
from bot.core.skills import Skill, SkillRegistry
from tests.fakes import make_state


def _registry():
    return SkillRegistry({
        "translate": Skill(name="translate", description="中英互译", body="body"),
    })


def _load_call(skill_name, call_id="c1"):
    return AIMessage(
        content="",
        tool_calls=[{"name": "load_skill", "args": {"skill_name": skill_name},
                     "id": call_id, "type": "tool_call"}],
    )


def test_loads_skill_into_active():
    state = make_state(messages=[
        _load_call("translate"),
        ToolMessage(content="正文", tool_call_id="c1"),
    ])
    result = asyncio.run(skill_manager_node(state, skill_registry=_registry()))
    assert result["active_skills"] == ["translate"]


def test_unloads_skill():
    state = make_state(active_skills=["translate"], messages=[
        AIMessage(content="", tool_calls=[
            {"name": "unload_skill", "args": {"skill_name": "translate"},
             "id": "c1", "type": "tool_call"}]),
        ToolMessage(content="已停用", tool_call_id="c1"),
    ])
    result = asyncio.run(skill_manager_node(state, skill_registry=_registry()))
    assert result["active_skills"] == []


def test_ignores_nonexistent_skill():
    state = make_state(active_skills=["translate"], messages=[
        _load_call("ghost"),
        ToolMessage(content="不存在", tool_call_id="c1"),
    ])
    result = asyncio.run(skill_manager_node(state, skill_registry=_registry()))
    assert result == {}  # 不写入 ghost，active 保持不变
    assert state["active_skills"] == ["translate"]


def test_skips_duplicate_load():
    state = make_state(active_skills=["translate"], messages=[_load_call("translate")])
    result = asyncio.run(skill_manager_node(state, skill_registry=_registry()))
    assert result == {}


def test_noop_without_skill_calls():
    state = make_state(messages=[
        AIMessage(content="", tool_calls=[
            {"name": "search_chat_history", "args": {"query": "x"},
             "id": "c1", "type": "tool_call"}]),
    ])
    result = asyncio.run(skill_manager_node(state, skill_registry=_registry()))
    assert result == {}


def test_noop_with_no_tool_calls():
    state = make_state(messages=[AIMessage(content="普通回复")])
    result = asyncio.run(skill_manager_node(state, skill_registry=_registry()))
    assert result == {}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_skill_manager.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 最小实现**

```python
# bot/core/nodes/action_node/skill_manager.py
"""skill_manager — 把 load_skill/unload_skill 的调用结果写回 BotState.active_skills。

工具本身（ToolNode 执行）只返回正文/确认；本节点从消息历史找到调用参数，
决定激活/释放哪些技能。无技能调用或无需变更时返回 {} 不打断工具循环。
"""

import logging

from langchain_core.messages import AIMessage

from object.bot.state import BotState

logger = logging.getLogger(__name__)


def _last_ai_with_tool_calls(messages) -> AIMessage | None:
    """从末尾向前找最后一个带 tool_calls 的 AIMessage。"""
    for m in reversed(messages):
        if isinstance(m, AIMessage) and getattr(m, "tool_calls", None):
            return m
    return None


async def skill_manager_node(state: BotState, skill_registry=None) -> dict:
    """扫描最近的工具调用，更新 active_skills。"""
    if skill_registry is None:
        return {}
    last_ai = _last_ai_with_tool_calls(state["messages"])
    if last_ai is None:
        return {}
    active = list(state.get("active_skills", []))
    changed = False
    for call in last_ai.tool_calls:
        name = call.get("name")
        args = call.get("args", {}) or {}
        if name == "load_skill":
            skill = args.get("skill_name", "")
            if skill_registry.has(skill) and skill not in active:
                active.append(skill)
                changed = True
        elif name == "unload_skill":
            skill = args.get("skill_name", "")
            if skill in active:
                active.remove(skill)
                changed = True
    if not changed:
        return {}
    logger.info("active_skills updated: %s", active)
    return {"active_skills": active}
```

`bot/core/nodes/action_node/__init__.py` 追加：

```python
from .skill_manager import skill_manager_node

__all__ = [
    "describe_image_node", "detect_intent", "index_turn_node", "skill_manager_node",
    "summarize_node",
]
```

`bot/core/nodes/__init__.py` 追加导出 `skill_manager_node`。

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_skill_manager.py -v`
Expected: PASS（6 passed）

- [ ] **Step 5: Commit**

```bash
git add bot/core/nodes/action_node/skill_manager.py bot/core/nodes/action_node/__init__.py bot/core/nodes/__init__.py tests/test_skill_manager.py
git commit -m "feat: skill_manager 节点把 load/unload 写回 active_skills（跨轮持久化）"
```

---

### Task 6: build_tools 注入技能工具

**Files:**
- Modify: `bot/core/tools/factory.py`
- Test: `tests/test_tools_factory.py`

**Interfaces:**
- Consumes: Task 3 `load_skill`/`unload_skill`、Task 1 `SkillRegistry`
- Produces:
  - `build_tools(rag_service=None, memory_store=None, mcp_tools=None, skill_registry=None) -> list[BaseTool]`
  - 新增工具名：`load_skill`、`unload_skill`（仅当 `skill_registry` 非空时注入）
- 关键行为：`load_skill` schema 只暴露 `skill_name`（无注入参数）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_tools_factory.py 末尾追加
"""技能工具：registry 非空才注入；schema 无注入参数。"""

from bot.core.skills import Skill, SkillRegistry


def test_skill_tools_present_when_registry_injected():
    registry = SkillRegistry({"translate": Skill(name="translate", description="中英互译", body="正文")})
    tools = build_tools(rag_service=None, memory_store=None, skill_registry=registry)
    assert {"load_skill", "unload_skill"} <= _names(tools)


def test_no_skill_tools_without_registry():
    tools = build_tools(rag_service=None, memory_store=None)
    assert "load_skill" not in _names(tools)


def test_no_skill_tools_when_empty_registry():
    tools = build_tools(rag_service=None, memory_store=None, skill_registry=SkillRegistry())
    assert "load_skill" not in _names(tools)


def test_load_skill_schema_only_has_skill_name():
    registry = SkillRegistry({"translate": Skill(name="translate", description="中英互译", body="正文")})
    tools = build_tools(rag_service=None, memory_store=None, skill_registry=registry)
    by_name = {t.name: t for t in tools}
    props = by_name["load_skill"].tool_call_schema.model_json_schema()["properties"]
    assert set(props) == {"skill_name"}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_tools_factory.py -v`
Expected: FAIL（`build_tools` 不接受 `skill_registry` 参数 → `TypeError`）

- [ ] **Step 3: 最小实现**

`bot/core/tools/factory.py`：

```python
from bot.core.skills.tools import load_skill, unload_skill
```

追加两个工具描述常量（放 `RECALL_TOOL_DESCRIPTION` 之后）：

```python
LOAD_SKILL_TOOL_DESCRIPTION = (
    "加载一个技能，返回其完整使用说明/规则正文。"
    "当用户需求匹配系统提示里某个技能描述（触发信号）时，先调用本工具取回正文再按其执行。"
    "技能加载后持续生效，直到调用 unload_skill 释放。"
)

UNLOAD_SKILL_TOOL_DESCRIPTION = (
    "释放一个已加载的技能，停止遵循其规则。技能不再需要（任务完成/话题偏离）时调用。幂等。"
)
```

追加构造助手（放 `_make_memory_tools` 之后）：

```python
def _make_skill_tools(skill_registry) -> list[BaseTool]:
    async def _load(
        skill_name: Annotated[str, Field(description="技能名（见系统提示的技能索引）")],
    ) -> str:
        try:
            return await load_skill(skill_name, skill_registry)
        except Exception:
            logger.exception("load_skill failed")
            return "工具执行失败。"

    async def _unload(
        skill_name: Annotated[str, Field(description="技能名")],
    ) -> str:
        try:
            return await unload_skill(skill_name)
        except Exception:
            logger.exception("unload_skill failed")
            return "工具执行失败。"

    return [
        StructuredTool.from_function(
            coroutine=_load, name="load_skill", description=LOAD_SKILL_TOOL_DESCRIPTION,
        ),
        StructuredTool.from_function(
            coroutine=_unload, name="unload_skill", description=UNLOAD_SKILL_TOOL_DESCRIPTION,
        ),
    ]
```

`build_tools` 签名与注入：

```python
def build_tools(rag_service=None, memory_store=None, mcp_tools=None,
                skill_registry=None) -> list[BaseTool]:
    tools: list[BaseTool] = []
    if rag_service is not None and rag_service.enabled:
        tools.append(_make_search_tool(rag_service))
    if memory_store is not None:
        tools += _make_memory_tools(memory_store)
    if skill_registry is not None and skill_registry.names():
        tools += _make_skill_tools(skill_registry)
    tools += list(mcp_tools or [])
    return tools
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_tools_factory.py -v`
Expected: PASS（原 6 个 + 新 4 个）

- [ ] **Step 5: Commit**

```bash
git add bot/core/tools/factory.py tests/test_tools_factory.py
git commit -m "feat: build_tools 注入 load_skill/unload_skill（registry 非空时）"
```

---

### Task 7: 图接线 + 集成测试 + 文档

**Files:**
- Modify: `object/bot/state.py`（加 `active_skills` 字段）
- Modify: `bot/core/graph.py`（注册 skill_manager 节点 + 改边 + 透传）
- Modify: `bot/core/nodes/llm_node/call_llm.py`（传 skill_registry + active_skills）
- Modify: `bot/core/nodes/action_node/summarize.py`（传 skill_registry + active_skills）
- Modify: `main.py`（构建 SkillRegistry）
- Modify: `CLAUDE.md`
- Test: `tests/test_graph.py`（新增两个集成测试）

**Interfaces:**
- Consumes: Task 4 `build_system_messages` 新签名、Task 5 `skill_manager_node`、Task 6 `build_tools` 新签名
- Produces:
  - `BotState.active_skills: list[str]`（只增不改；**不设 reducer**——skill_manager 唯一写入者，last-write-wins）
  - `create_graph(llm, config, db_dir="db", rag_service=None, memory_store=None, vision_service=None, mcp_tools=None, skill_registry=None)` 
  - 图边：`tools → skill_manager → call_llm`
  - `call_llm_node(state, llm, tools=None, use_memory=False, use_mcp=False, bot_config=None, skill_registry=None)`（加默认参数）
  - `summarize_node(state, llm, bot_config, skill_registry=None)`（加默认参数）

- [ ] **Step 1: 写失败测试（图集成）**

```python
# tests/test_graph.py 末尾追加
"""技能模块集成：加载持久化 + 线程隔离 + 注入可见。"""

from bot.core.skills import Skill, SkillRegistry

SKILL_LOAD_CALLS = [
    {"name": "load_skill", "args": {"skill_name": "translate"},
     "id": "call_skill_1", "type": "tool_call"},
]


def _skill_registry():
    return SkillRegistry({
        "translate": Skill(name="translate", description="中英互译", body="翻译规则：保留语气"),
    })


def test_graph_skill_persists_across_turns(tmp_path):
    llm = ScriptedLLM([
        # 第 1 轮：加载技能
        AIMessage(content="", tool_calls=SKILL_LOAD_CALLS),
        AIMessage(content="已启用翻译技能"),
        # 第 2 轮（同 thread）：直接翻译
        AIMessage(content="翻译：你好 → Hello"),
    ])
    graph, _ = asyncio.run(create_graph(
        llm, BotConfig(skills_enabled=True), db_dir=str(tmp_path),
        skill_registry=_skill_registry(),
    ))
    state1 = {**_initial_state(), "llm_text": "用翻译技能", "clean_text": "用翻译技能"}
    r1 = asyncio.run(graph.ainvoke(state1, {"configurable": {"thread_id": "test:thread"}}))
    assert r1["active_skills"] == ["translate"]

    # 第 2 轮不带 active_skills（输入覆盖 checkpoint 会导致清零——这是设计约束）
    state2 = {**_initial_state(), "llm_text": "翻译 how are you", "clean_text": "翻译 how are you"}
    r2 = asyncio.run(graph.ainvoke(state2, {"configurable": {"thread_id": "test:thread"}}))
    assert r2["active_skills"] == ["translate"]  # checkpoint 恢复
    sys_msgs = [m for m in llm.last_messages if isinstance(m, SystemMessage)]
    assert any("翻译规则：保留语气" in m.content for m in sys_msgs)  # 正文注入可见


def test_graph_skill_isolated_per_thread(tmp_path):
    llm = ScriptedLLM([
        AIMessage(content="", tool_calls=SKILL_LOAD_CALLS),
        AIMessage(content="已启用"),
        AIMessage(content="普通回复"),  # 线程 B
    ])
    graph, _ = asyncio.run(create_graph(
        llm, BotConfig(skills_enabled=True), db_dir=str(tmp_path),
        skill_registry=_skill_registry(),
    ))
    a = {**_initial_state(), "llm_text": "翻译", "clean_text": "翻译"}
    asyncio.run(graph.ainvoke(a, {"configurable": {"thread_id": "thread:A"}}))

    b = {**_initial_state(), "llm_text": "你好", "clean_text": "你好"}
    rb = asyncio.run(graph.ainvoke(b, {"configurable": {"thread_id": "thread:B"}}))
    assert rb.get("active_skills", []) == []  # 新线程不串技能
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_graph.py -v`
Expected: FAIL（`BotConfig` 无 `skills_enabled`？已有；`create_graph` 无 `skill_registry` 参数 → `TypeError`）

- [ ] **Step 3: 实现接线**

`object/bot/state.py` — 在 `tool_rounds` 后追加字段：

```python
    tool_rounds: int       # 工具调用轮次计数（call_llm 递增，工具回环上限）
    active_skills: list[str]  # 已激活技能名（skill_manager 写、build_system_messages 注入正文；handler 绝不注入）
```

`bot/core/graph.py`：

```python
from bot.core.nodes import (
    call_llm_node,
    describe_image_node,
    detect_intent,
    index_turn_node,
    skill_manager_node,
    summarize_node,
)
```

`create_graph` 签名加 `skill_registry=None`；tools 组装加参数：

```python
    tools = build_tools(
        rag_service=rag_service, memory_store=memory_store, mcp_tools=mcp_tools,
        skill_registry=skill_registry,
    )
```

`call_llm` 节点 partial 加 `skill_registry=skill_registry`；新增 skill_manager 节点：

```python
    builder.add_node(
        "call_llm", partial(
            call_llm_node,
            llm=llm,
            tools=tools,
            use_memory=use_memory,
            use_mcp=use_mcp,
            bot_config=config,
            skill_registry=skill_registry,
        )
    )
    builder.add_node("summarize", partial(
        summarize_node, llm=llm, bot_config=config, skill_registry=skill_registry,
    ))
    builder.add_node("skill_manager", partial(skill_manager_node, skill_registry=skill_registry))
```

边改动（唯一一处）：

```python
    builder.add_edge("tools", "skill_manager")
    builder.add_edge("skill_manager", "call_llm")
```

（`builder.add_edge("tools", "call_llm")` 改为上面两条。）

`bot/core/nodes/llm_node/call_llm.py` — 签名加默认参数，注入透传：

```python
async def call_llm_node(
    state: BotState,
    llm: ChatOpenAI,
    tools: list[BaseTool] | None = None,
    use_memory: bool = False,
    use_mcp: bool = False,
    bot_config: BotConfig | None = None,
    skill_registry=None,
) -> dict:
    ...
    system_msgs = build_system_messages(
        persona, summary,
        skill_registry=skill_registry,
        active_skills=state.get("active_skills", []),
    )
```

`bot/core/nodes/action_node/summarize.py` — 签名加默认参数，估算透传：

```python
async def summarize_node(
    state: BotState,
    llm: ChatOpenAI,
    bot_config: BotConfig,
    skill_registry=None,
) -> dict:
    ...
    total = estimate_context_tokens(
        state["messages"],
        state.get("persona", ""),
        state.get("conversation_summary", ""),
        skill_registry=skill_registry,
        active_skills=state.get("active_skills", []),
    )
```

`main.py` — import + 构建注册表 + 透传：

```python
from bot.core.skills import SkillRegistry

    skill_registry = None
    if config.skills_enabled:
        skill_registry = SkillRegistry.from_directory(
            config.skills_dir, index_max=config.skills_index_max,
        )
        logger.info("Loaded %d skills from %s", skill_registry.total, config.skills_dir)

    graph, _ = await create_graph(
        llm, config, db_dir=config.db_dir, rag_service=rag_service, memory_store=memory_store,
        vision_service=vision_service, mcp_tools=mcp_tools, skill_registry=skill_registry,
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_graph.py -v`
Expected: PASS（原 8 个 + 新 2 个）

- [ ] **Step 5: 全量回归 + lint**

Run: `uv run pytest -v`
Expected: PASS（全部既有测试 + 新增测试）
Run: `uv run ruff check`
Expected: 0 错误（保持 lint 清零现状）

- [ ] **Step 6: CLAUDE.md 同步**

在 `Data flow` 中把工具回环改为 `→ tools（ToolNode）→ skill_manager → call_llm`；在架构树 `action_node/` 下加 `skill_manager.py` 一行；新增一节「技能模块（提示词包）」：SkillRegistry 启动扫描、`load_skill`/`unload_skill` 纯函数、`active_skills` 写回与注入层、`BOT_SKILLS_ENABLED`/`BOT_SKILLS_DIR`/`BOT_SKILLS_INDEX_MAX`。在 SystemMessage 层级段补「技能索引 + 激活正文」两层。

- [ ] **Step 7: Commit**

```bash
git add object/bot/state.py bot/core/graph.py bot/core/nodes/llm_node/call_llm.py bot/core/nodes/action_node/summarize.py main.py CLAUDE.md tests/test_graph.py
git commit -m "feat: 接线 skill 模块到图（skill_manager 节点 + active_skills 注入层 + 集成测试 + CLAUDE.md）"
```

---

## 自检结果（写作计划时对照 spec 核查）

- **Spec 覆盖**：SkillRegistry（Task 1）、config+prompts（Task 2）、load/unload 工具（Task 3）、注入层（Task 4）、skill_manager 节点（Task 5）、build_tools（Task 6）、图接线+集成（Task 7）——spec 的每个文件变更行都有对应任务。
- **占位符**：无 TBD/TODO；每个代码步骤都给出完整可执行代码与精确期望输出。
- **类型一致**：`SkillRegistry`/`Skill`/`load_skill`/`unload_skill`/`skill_manager_node` 的签名在 Task 1/3/5/7 间一致；`build_system_messages`/`estimate_context_tokens` 新参数在 Task 4/7 一致；`build_tools` 与 `create_graph` 的 `skill_registry` 参数在 Task 6/7 一致。
- **相对 spec 的设计修正**（已反映到上述任务）：(1) handler **不**注入 `active_skills`（输入覆盖 checkpoint 会清零跨轮持久化）；(2) `SkillRegistry` 可内存构造，无需 `StubSkillRegistry`；(3) 技能工具不需要 `thread_id` 注入。
