# content_parser 解耦：领域类型入 object/bot 设计

**日期**：2026-08-02
**状态**：已获用户确认（全放 object/bot/；函数留 utils）

## Context

`bot/core/utils/content_parser.py` 混居两类东西：**领域数据对象**（`MessageKind` 枚举、`Attachment`、`ParsedContent` 数据类）和**纯解析函数**（`parse_content` / `clean_text` / `to_llm_text` / ...）。

引发重构的真实耦合不在 `content_parser` 内部（它已是零依赖的叶子模块），而在 `object/bot/state.py`：

```python
content_kind: str       # MessageKind.value: "text"/"image"/"file"/"audio"/"video"
```

`BotState` 用裸字符串 + 注释指代 `MessageKind`，但 `object/` 层**不能 import** `bot.core.utils`——依赖方向必须保持 `bot.core → object`。于是 `MessageKind` 只能活在注释里，领域类型没有一个可被各层共同引用的规范落点。

**目标**：把领域类型放到 `object/` 层（依赖自由的协议数据对象层），让 `object/bot/state.py` 能引用到真实存在的规范类型；`bot/core/utils` 退化为纯解析逻辑层。

**已排除**：`common/` 是 config + prompts 的单一来源（`common/__init__.py` docstring 明示），没有数据对象先例，不放入。

## 决策

1. **类型全放 `object/bot/content.py`**（新增，纯数据对象，零外部依赖）
2. **解析函数留 `bot/core/utils/content_parser.py`**，改从 `object.bot.content` import 类型
3. **`BotState.content_kind` 保持 `str`**，继续存 `.value`——避免把 `MessageKind` 枚举写进 checkpoint 重蹈 `ChannelType` 未注册类型序列化警告

## 改动清单

### 1. 新增 `object/bot/content.py`
从 `content_parser.py` **原样搬移**（只带 dataclass 依赖，不含任何解析逻辑）：

```python
"""Satori 消息内容分类的领域类型（零逻辑，供各层共享引用）。"""

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

`_PLACEHOLDERS` / `_TAG_TO_KIND` 等映射是解析逻辑，**不搬**。

### 2. 修改 `bot/core/utils/content_parser.py`
- 删除 `MessageKind` / `Attachment` / `ParsedContent` 定义及 `dataclass`、`Enum` import
- 顶部新增 `from object.bot.content import Attachment, MessageKind, ParsedContent`
- 函数与正则全部原地不动

### 3. Lazy-loading 接线（遵循 CLAUDE.md 记录的既定模式）
- `object/bot/__init__.py`：`__all__` 加 `"Attachment"` / `"MessageKind"` / `"ParsedContent"`；`_module_map` 三者映射到 `"content"`
- `object/__init__.py`：三名字加进 `__all__` 与 `_module_map`（映射到 `"bot"`）

### 4. 兼容层（零破坏）
- `bot/core/utils/__init__.py` 门面**继续 re-export** 三个类型（`from object.bot.content import ...`）
- grep 确认 `handler.py`、`index_turn.py`、`tests/test_content_parser.py` 全部经门面消费，**无一直连 `content_parser` 模块** → 外部代码一行不改

### 5. 状态注释
`object/bot/state.py:37` 注释更新为指向真实类型：`content_kind: str  # MessageKind.value（object.bot.content.MessageKind）`

### 6. 文档
`CLAUDE.md` 架构段：`bot/core/utils/content_parser.py` 一行注明"领域类型见 `object/bot/content.py`"。

## 测试

- 现有 `tests/test_content_parser.py` **不改**（经门面 import，行为不变）
- 新增 `tests/test_object_content.py` 锁定接线：
  - `from object import MessageKind, ParsedContent, Attachment` 可导入
  - 其模块为 `object.bot.content` 的真实类（非门面副本）
  - `MessageKind.IMAGE.value == "image"`（`str, Enum` 值不变）

## 不改动

- `common/`：保持纯 config + prompts
- `object/bot/state.py` 字段类型：保持 `str`（序列化安全）
- 任何函数签名 / 行为：`parse_content` 返回类型等保持原样

## 风险

- **零行为变更**：类型原样搬移，门面 re-export 兜底，无 checkpoint 格式变化
- **依赖方向**：`bot.core.utils → object.bot` 是既有正向方向，无环
- lazy-loading 接线漏配会导致 `from object import MessageKind` 抛 `AttributeError`——由新增测试兜底

## 验证

```
uv run pytest tests/ -q       # 全量回归
```
