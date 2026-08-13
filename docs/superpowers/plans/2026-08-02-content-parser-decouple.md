# content_parser 解耦实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `MessageKind`/`Attachment`/`ParsedContent` 三个领域类型从 `bot/core/utils/content_parser.py` 迁入 `object/bot/content.py`，让 `object/` 层（含 `BotState`）能引用规范类型，`utils` 退化为纯解析逻辑层。

**Architecture:** 新增零依赖的数据对象模块 `object/bot/content.py`；`content_parser.py` 改为 `from object.bot.content import ...`；经既有 lazy-loading 模式（`object/bot/__init__.py` + `object/__init__.py` 的 `__all__` 与 `_module_map`）对外导出；`bot/core/utils/__init__.py` 门面继续 re-export 保证零破坏。

**Tech Stack:** Python 3.12, dataclasses, enum, pytest, uv。

## Global Constraints

- `object/bot/content.py` 必须是**纯数据对象**：只含 `MessageKind`/`Attachment`/`ParsedContent` 及 dataclass/enum import，不含任何解析逻辑、无外部依赖。
- `MessageKind` 保持 `class MessageKind(str, Enum)`，值不变（`text`/`image`/`file`/`audio`/`video`）。
- `BotState.content_kind` 字段**保持 `str`**（存 `.value`），不得改为存储 `MessageKind` 枚举（避免重蹈 `ChannelType` checkpoint 序列化警告）。
- 解析函数（`parse_content`/`clean_text`/`to_llm_text`/`parse_attachments`/`classify_content`）**留在** `bot/core/utils/content_parser.py`。
- `bot/core/utils/__init__.py` 门面对外导出的名字集合**保持不变**（handler / index_turn / 测试经门面消费，零破坏）。
- `common/` 不改动。
- 现有 `tests/test_content_parser.py` 不改动、必须继续通过。
- 验证命令：`uv run pytest tests/ -q` 全量通过（当前基线 100 passed, 1 skipped）。

---
---

### Task 1: 领域类型入 `object/bot/content.py` + lazy-loading 接线

**Files:**
- Create: `object/bot/content.py`
- Modify: `object/bot/__init__.py`
- Modify: `object/__init__.py`
- Create: `tests/test_object_content.py`

**Interfaces:**
- Consumes: 无（零依赖模块）
- Produces: `object.bot.content.MessageKind` / `Attachment` / `ParsedContent`，经 `object.bot` 与 `object` 包 lazy-loading 均可导入

- [ ] **Step 1: 写接线失败测试**

`tests/test_object_content.py`（新建）：
```python
"""锁定领域类型经 object 包 lazy-loading 导出，且为 object.bot.content 的真实类。"""

from object import Attachment, MessageKind, ParsedContent
import object.bot.content as content_module


def test_message_kind_values():
    assert MessageKind.TEXT.value == "text"
    assert MessageKind.IMAGE.value == "image"
    assert MessageKind.FILE.value == "file"
    assert MessageKind.AUDIO.value == "audio"
    assert MessageKind.VIDEO.value == "video"
    assert MessageKind("image") is MessageKind.IMAGE  # str, Enum 反查


def test_types_live_in_object_bot_content():
    assert MessageKind.__module__ == "object.bot.content"
    assert ParsedContent.__module__ == "object.bot.content"
    assert Attachment.__module__ == "object.bot.content"


def test_parsed_content_has_media():
    img = ParsedContent(kind=MessageKind.IMAGE, attachments=[Attachment(type="img", src="x")])
    assert img.has_media is True
    txt = ParsedContent(kind=MessageKind.TEXT)
    assert txt.has_media is False
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_object_content.py -q`
Expected: FAIL — `from object import MessageKind` 抛 `AttributeError`（未接线）

- [ ] **Step 3: 创建 `object/bot/content.py`**

```python
"""Satori 消息内容分类的领域类型（纯数据对象，零逻辑，供各层共享引用）。

由 ``bot.core.utils.content_parser`` 的解析函数消费；解析逻辑留在 utils，
这里只存放可被 ``object/`` 层（含 ``BotState``）引用的规范类型。
"""

from dataclasses import dataclass, field
from enum import Enum


class MessageKind(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    FILE = "file"
    AUDIO = "audio"
    VIDEO = "video"


@dataclass
class Attachment:
    type: str               # img / file / audio / video（标签名）
    name: str = ""          # 文件名（file 标签的 name 属性）
    src: str = ""           # 资源地址（已 unescape）
    start: int = 0
    end: int = 0


@dataclass
class ParsedContent:
    kind: MessageKind       # 主类型：首个媒体标签决定
    attachments: list[Attachment] = field(default_factory=list)
    clean_text: str = ""    # 剥全部标签、unescape、折叠空白（RAG 用）
    llm_text: str = ""      # 媒体→占位符、剥 at（LLM 用）
    has_text: bool = False

    @property
    def has_media(self) -> bool:
        return bool(self.attachments)
```

- [ ] **Step 4: 接线 lazy-loading**

`object/bot/__init__.py` — `__all__` 与 `_module_map` 各加三个名字：
```python
__all__ = [
    "BotState",
    "Attachment",
    "MessageKind",
    "ParsedContent",
]

_module_map = {
    "BotState": "state",
    "Attachment": "content",
    "MessageKind": "content",
    "ParsedContent": "content",
}
```

`object/__init__.py` — `__all__` 加三个名字（放在 `# bot` 注释组下），并把 `_module_map` 生成循环的 bot 判定改为集合：
```python
__all__ = [
    # bot
    "BotState",
    "Attachment",
    "MessageKind",
    "ParsedContent",
    # satori — enums
    ...
]

_BOT_NAMES = {"BotState", "Attachment", "MessageKind", "ParsedContent"}
for _name in __all__:
    _module_map[_name] = "bot" if _name in _BOT_NAMES else "satori"
```
> 注意：原循环是 `"bot" if _name == "BotState" else "satori"`，直接加名字到 `__all__` 会被误映射到 `satori`，必须同时改判定。

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/test_object_content.py -q`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add object/bot/content.py object/bot/__init__.py object/__init__.py tests/test_object_content.py
git commit -m "feat: 领域类型 MessageKind/Attachment/ParsedContent 入 object/bot/content（lazy-loading 接线）"
```

---
---

### Task 2: content_parser 改 import 类型 + 状态注释 + 文档同步

**Files:**
- Modify: `bot/core/utils/content_parser.py`
- Modify: `bot/core/utils/__init__.py`
- Modify: `object/bot/state.py:37`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: `object.bot.content`（Task 1 产物）
- Produces: `bot.core.utils` 门面同名导出不变（`Attachment`/`MessageKind`/`ParsedContent`/`classify_content`/`clean_text`/`parse_attachments`/`parse_content`/`to_llm_text`）

- [ ] **Step 1: `content_parser.py` 删本地类型定义、改 import**

- 删除 `from dataclasses import dataclass, field` 与 `from enum import Enum` 两行，改为：
  ```python
  from object.bot.content import Attachment, MessageKind, ParsedContent
  ```
- 删除三个类定义块：`class MessageKind(str, Enum):` 至其结束、`@dataclass\nclass Attachment:` 至其结束、`@dataclass\nclass ParsedContent:`（含 `has_media` property）至其结束
- **保留**：`import html` / `import re`、四个正则、`_PLACEHOLDERS`、`_TAG_TO_KIND`、全部函数
- 模块 docstring 末尾追加一行：
  ```
  类型定义（MessageKind/Attachment/ParsedContent）见 ``object.bot.content``。
  ```

- [ ] **Step 2: 门面 `bot/core/utils/__init__.py` 类型改从 object 导入**

类型与函数分开导入，导出集合不变：
```python
from bot.core.utils.context import estimate_context_tokens, format_messages_for_summary
from bot.core.utils.content_parser import (
    classify_content,
    clean_text,
    parse_attachments,
    parse_content,
    to_llm_text,
)
from object.bot.content import Attachment, MessageKind, ParsedContent

__all__ = [
    "Attachment",
    "MessageKind",
    "ParsedContent",
    "classify_content",
    "clean_text",
    "estimate_context_tokens",
    "format_messages_for_summary",
    "parse_attachments",
    "parse_content",
    "to_llm_text",
]
```

- [ ] **Step 3: 运行现有测试确认零破坏**

Run: `uv run pytest tests/test_content_parser.py tests/test_object_content.py tests/test_graph.py tests/test_handler_media.py -q`
Expected: 全 PASS（test_content_parser 证明门面行为不变；graph/handler_media 覆盖 index_turn/handler 消费方）

- [ ] **Step 4: 状态注释 + 文档**

`object/bot/state.py:37` 注释改为指向真实类型：
```python
content_kind: str       # object.bot.content.MessageKind.value: "text"/"image"/"file"/"audio"/"video"
```

`CLAUDE.md` 两处：
- `object/bot/` 树中加一行：`content.py              #   MessageKind/Attachment/ParsedContent（消息分类领域类型）`
- `bot/core/utils/` 树中 `content_parser.py` 行改为：`content_parser.py      #   Satori content 解析逻辑（clean_text / to_llm_text；类型见 object/bot/content.py）`

- [ ] **Step 5: 全量回归**

Run: `uv run pytest tests/ -q`
Expected: 100 passed, 1 skipped

- [ ] **Step 6: Commit**

```bash
git add bot/core/utils/content_parser.py bot/core/utils/__init__.py object/bot/state.py CLAUDE.md
git commit -m "refactor: content_parser 改为 import object.bot.content 类型，utils 退化为纯解析逻辑"
```
