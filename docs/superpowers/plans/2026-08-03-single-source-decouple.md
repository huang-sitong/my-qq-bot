# 单一事实来源解耦实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把五处重复的单一事实来源收敛到唯一落点：路由判定表、摘要层 SystemMessage、提示词常量、`[图片]` 占位符、`content_kind` 魔法字符串。

**Architecture:** 新增零依赖纯函数模块 `bot/core/utils/routing.py` 承载完整回复判定表，`detect_intent` 与 `graph._route_after_detect` 共同消费；`utils/context.py` 新增 `build_system_messages()` 让 call_llm 与 token 估算共用同一构造；`VISION_PROMPT`/`RETRIEVAL_TASK` 迁回 `common/prompts.py`（common 声明的单一事实来源）；`IMAGE_PLACEHOLDER` 由 content_parser 导出、describe_image 引用；`content_kind` 比较改用 `MessageKind.X.value`。

**Tech Stack:** Python 3.12, langchain_core, pytest, uv。

## Global Constraints

- `bot/core/utils/routing.py` 是**纯函数叶模块**：只 import `object.bot.content.MessageKind` / `object.satori.ChannelType`，**不 import langgraph**（`route_after_detect` 返回 `None` 表示 END，由 `graph.py` 映射）。
- 现有测试**不改**（`test_detect_intent` / `test_graph` / `test_handler_media` / `test_call_llm_node` 等保持原样绿色，作为行为兼容的集成护栏）。唯一例外：`tests/test_vision_service.py:8` 的 `VISION_PROMPT` import 改从 `common` 拉。
- 门面 `bot.core.utils.__init__` 导出集合**只增不减**：新增 `IMAGE_PLACEHOLDER` 与 `build_system_messages`，既有的 10 个名字与顺序不变。
- `BotState` 字段、checkpoint 格式、图接线顺序、`handler.py` 初始 state、`bot/core/nodes/llm_node/router.py` 死代码**均不改动**。
- 验证命令：`uv run pytest tests/ -q` 全量通过（当前基线 103 passed, 1 skipped）。

---
---

### Task 1: 路由判定单一来源 `routing.py`（唯一行为改动）

**Files:**
- Create: `bot/core/utils/routing.py`
- Modify: `bot/core/nodes/action_node/detect_intent.py`
- Modify: `bot/core/graph.py`
- Create: `tests/test_routing.py`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: `object.bot.content.MessageKind`（枚举）、`object.satori.ChannelType`（DIRECT）
- Produces: `bot.core.utils.routing` 的四个导出——
  - `decide_reply(channel_type: int, content_kind: str, bot_id: str, raw_content: str) -> bool`（should_respond）
  - `keep_in_context(should_respond: bool, content_kind: str) -> bool`
  - `route_after_detect(should_respond: bool, content_kind: str) -> str | None`（None=END）
  - `NON_REPLY_KINDS: frozenset[str]`

- [ ] **Step 1: 写失败测试 `tests/test_routing.py`**

```python
"""路由判定单一来源：是否回复 / 是否入上下文 / detect_intent 后三路路径。

锁定 bot.core.utils.routing 的完整判定表——detect_intent 与 graph 共同消费，
此处是唯一权威，行为若偏离这里即为回归。
"""

import pytest

from bot.core.utils.routing import decide_reply, keep_in_context, route_after_detect
from object.satori import ChannelType


# --- decide_reply ---

@pytest.mark.parametrize("kind", ["file", "audio", "video"])
def test_media_never_reply_even_direct_or_mention(kind):
    assert decide_reply(ChannelType.DIRECT, kind, "bot1", f'<at id="bot1"/><{kind} src="x"/>') is False


def test_direct_text_replies():
    assert decide_reply(ChannelType.DIRECT, "text", "bot1", "你好") is True


def test_direct_image_replies():
    assert decide_reply(ChannelType.DIRECT, "image", "bot1", '<img src="x"/>') is True


def test_group_mention_replies():
    assert decide_reply(0, "text", "bot1", '<at id="bot1"/> 你好') is True


def test_group_without_mention_does_not_reply():
    assert decide_reply(0, "text", "bot1", "晚上吃什么") is False


def test_group_image_without_mention_no_reply():
    assert decide_reply(0, "image", "bot1", '<img src="x"/>') is False


# --- keep_in_context ---

def test_keep_when_replying():
    assert keep_in_context(True, "image") is True


def test_keep_non_reply_text():
    assert keep_in_context(False, "text") is True


def test_not_keep_non_reply_media():
    assert keep_in_context(False, "image") is False
    assert keep_in_context(False, "file") is False


# --- route_after_detect ---

def test_reply_routes_to_describe_image():
    assert route_after_detect(True, "text") == "describe_image"


def test_non_reply_text_routes_to_summarize():
    assert route_after_detect(False, "text") == "summarize"


def test_non_reply_media_routes_to_none():
    assert route_after_detect(False, "image") is None
    assert route_after_detect(False, "file") is None
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_routing.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.core.utils.routing'`

- [ ] **Step 3: 创建 `bot/core/utils/routing.py`**

```python
"""确定性回复判定（LLM router 架空后的唯一判定表）。

detect_intent 与 graph._route_after_detect 共同消费，消除双处同步。
纯函数：不 import langgraph；route_after_detect 返回 None 表示 END，由 graph 映射。
"""

from object.bot.content import MessageKind
from object.satori import ChannelType

# 永不回复的媒体类型（file/audio/video，即使私聊/@ 也盖不过）
NON_REPLY_KINDS = frozenset({
    MessageKind.FILE.value,
    MessageKind.AUDIO.value,
    MessageKind.VIDEO.value,
})


def decide_reply(channel_type: int, content_kind: str, bot_id: str, raw_content: str) -> bool:
    """should_respond：媒体永不回复；私聊回复；群聊仅 @ 时回复。"""
    if content_kind in NON_REPLY_KINDS:
        return False
    if channel_type == ChannelType.DIRECT:
        return True
    return bool(bot_id and f'<at id="{bot_id}"' in raw_content)


def keep_in_context(should_respond: bool, content_kind: str) -> bool:
    """非回复媒体不入上下文（占位符防污染）；非回复文本仍入上下文待压缩。"""
    return should_respond or content_kind == MessageKind.TEXT.value


def route_after_detect(should_respond: bool, content_kind: str) -> str | None:
    """detect_intent 之后的三路路径；None 表示 END（由 graph 映射）。"""
    if should_respond:
        return "describe_image"
    if content_kind == MessageKind.TEXT.value:
        return "summarize"
    return None
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_routing.py -q`
Expected: 14 passed

- [ ] **Step 5: `detect_intent.py` 改消费判定函数**

`bot/core/nodes/action_node/detect_intent.py` 头部 import 改为：
```python
import logging

from langchain_core.messages import HumanMessage

from bot.core.utils.routing import decide_reply, keep_in_context
from object.bot.state import BotState
from object.satori import ChannelType
```
`detect_intent` 函数体的判定块改为：
```python
    content_kind = state.get("content_kind", "")

    # 判定表（decide_reply / keep_in_context）单一来源见 bot.core.utils.routing
    should_respond = decide_reply(channel_type, content_kind, bot_id, raw_content)
```
末尾 `add_to_context` 行改为：
```python
    add_to_context = keep_in_context(should_respond, content_kind)
```
其余（HumanMessage 构建、`_strip_mention`、返回 dict、logger.debug）**保持原样**。`from object.satori import ChannelType` **保留**（`is_group = channel_type != ChannelType.DIRECT` 仍用）。

- [ ] **Step 6: `graph.py` 改委托 `route_after_detect`**

`bot/core/graph.py` 顶部加：
```python
from bot.core.utils.routing import route_after_detect
```
`_route_after_detect` 整体替换为：
```python
def _route_after_detect(state: BotState) -> str:
    """Deterministic 3-way route（判定表单一来源见 bot.core.utils.routing）。

    - should_respond → describe_image (vision for image turns, no-op for text) → call_llm
    - non-replied text → summarize (context + compression + single-record index)
    - non-replied media (image group non-@ / file / audio / video) → END
    """
    return route_after_detect(
        state.get("should_respond", False),
        state.get("content_kind", ""),
    ) or END
```
`END` 已 import，无需改动。其余图接线不变。

- [ ] **Step 7: 定向回归**

Run: `uv run pytest tests/test_routing.py tests/test_detect_intent.py tests/test_graph.py tests/test_handler_media.py -q`
Expected: 全 PASS（test_detect_intent / test_graph 证明行为不变；test_handler_media 覆盖索引路径）

- [ ] **Step 8: CLAUDE.md 同步**

`utils/` 树加一行（对齐到列 30）：
```
      routing.py             #   确定性回复判定（decide_reply / keep_in_context / route_after_detect）
```
Gotchas「回复判定树」段落末句 `注意：`detect_intent` 的 `add_to_context` 与 `graph._route_after_detect` 两处逻辑需同步。` 改为：
```
判定表单一来源为 `bot/core/utils/routing.py`（`decide_reply` / `keep_in_context` / `route_after_detect`），
`detect_intent` 与 `graph._route_after_detect` 共同消费，不再需要手动同步。
```

- [ ] **Step 9: Commit**

```bash
git add bot/core/utils/routing.py bot/core/nodes/action_node/detect_intent.py bot/core/graph.py tests/test_routing.py CLAUDE.md
git commit -m "refactor: 路由判定单一来源 routing.py，detect_intent/graph 共同消费"
```

---
---

### Task 2: 摘要层 SystemMessage 共享构建

**Files:**
- Modify: `bot/core/utils/context.py`
- Modify: `bot/core/nodes/llm_node/call_llm.py`
- Modify: `bot/core/utils/__init__.py`
- Create: `tests/test_context.py`

**Interfaces:**
- Consumes: 无新依赖
- Produces: `bot.core.utils.context.build_system_messages(persona: str, summary: str = "") -> list[SystemMessage]`，经门面导出

- [ ] **Step 1: 写失败测试 `tests/test_context.py`**

```python
"""build_system_messages：摘要层构造单一来源，call_llm 与 token 估算共用。"""

from langchain_core.messages import SystemMessage

from bot.core.utils import build_system_messages


def test_builds_persona_and_summary_layers():
    msgs = build_system_messages("你是助手", "之前聊过猫")
    assert [m.content for m in msgs] == ["你是助手", "之前的对话摘要：\n之前聊过猫"]
    assert all(isinstance(m, SystemMessage) for m in msgs)


def test_skips_empty_summary():
    msgs = build_system_messages("你是助手", "   ")
    assert [m.content for m in msgs] == ["你是助手"]


def test_skips_empty_persona():
    assert build_system_messages("   ", "摘要") == []
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_context.py -q`
Expected: FAIL — `ImportError: cannot import name 'build_system_messages'`

- [ ] **Step 3: `context.py` 加 `build_system_messages` 并重构 `estimate_context_tokens`**

`bot/core/utils/context.py` 在 `estimate_context_tokens` 之前新增：
```python
def build_system_messages(persona: str, summary: str = "") -> list[SystemMessage]:
    """构建 call_llm 的前两层 SystemMessage；estimate_context_tokens 复用保证估算一致。

    与 ``call_llm_node`` 注入的层级结构完全相同——token 估算与实际上下文永不偏离。
    """
    msgs = [SystemMessage(content=persona)] if persona.strip() else []
    if summary.strip():
        msgs.append(SystemMessage(content=f"之前的对话摘要：\n{summary}"))
    return msgs
```
`estimate_context_tokens` 的 Layer 0/Layer 1 构造替换为对它的调用：
```python
    # Layer 0 + 1: persona + conversation summary（构造与 call_llm 共用 build_system_messages）
    all_msgs = build_system_messages(persona, summary)

    # Layer 2..N: recent messages
    all_msgs.extend(messages)

    return count_tokens_approximately(
        all_msgs,
        chars_per_token=_CHARS_PER_TOKEN,
    )
```
（删除原先 `if persona.strip():` / `if summary.strip():` 两段，其余不变。）

- [ ] **Step 4: 门面 `bot/core/utils/__init__.py` 导出**

`from bot.core.utils.context import estimate_context_tokens, format_messages_for_summary` 改为：
```python
from bot.core.utils.context import (
    build_system_messages,
    estimate_context_tokens,
    format_messages_for_summary,
)
```
`__all__` 在 `"Attachment"` 之后加 `"build_system_messages"`。

- [ ] **Step 5: 运行确认通过**

Run: `uv run pytest tests/test_context.py -q`
Expected: 3 passed

- [ ] **Step 6: `call_llm.py` 改用共享构建**

`bot/core/nodes/llm_node/call_llm.py` 顶部加：
```python
from bot.core.utils import build_system_messages
```
`call_llm_node` 内三行 SystemMessage 构造替换为：
```python
    persona = state["persona"].format(bot_name=state.get("bot_name", ""))
    summary = state.get("conversation_summary", "").strip()
    system_msgs = build_system_messages(persona, summary)

    use_rag = rag_service is not None and rag_service.enabled
    use_memory = memory_store is not None
    if use_memory:
        system_msgs.append(SystemMessage(content=MEMORY_TOOL_HINT))
```
（`SystemMessage` import 仍用于 MEMORY_TOOL_HINT，保留；`use_rag`/`use_memory`/`messages = system_msgs + state["messages"]` 及其余不变。）

- [ ] **Step 7: 定向回归**

Run: `uv run pytest tests/test_context.py tests/test_call_llm_node.py tests/test_graph.py -q`
Expected: 全 PASS（test_call_llm_node 证明三层 SystemMessage 结构不变；test_graph 覆盖完整图）

- [ ] **Step 8: Commit**

```bash
git add bot/core/utils/context.py bot/core/utils/__init__.py bot/core/nodes/llm_node/call_llm.py tests/test_context.py
git commit -m "refactor: 摘要层 SystemMessage 构建收敛 build_system_messages，token 估算与实际注入共用"
```

---
---

### Task 3: 提示词归位 `common/prompts.py`

**Files:**
- Modify: `common/prompts.py`
- Modify: `common/__init__.py`
- Modify: `bot/core/vision/service.py`
- Modify: `bot/core/rag/embedder.py`
- Modify: `tests/test_vision_service.py`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: 无
- Produces: `common.VISION_PROMPT` / `common.RETRIEVAL_TASK`（从服务文件迁入，值不变）

- [ ] **Step 1: `common/prompts.py` 加两个常量**

文件末尾追加：
```python
# 视觉描述提示词（VisionService 用，/api/generate 的 prompt）
VISION_PROMPT = "请用中文简要描述这张图片的内容。"

# 嵌入检索任务前缀（EmbeddingService 用，Query 与 Document 共用保持向量空间一致）
RETRIEVAL_TASK = "检索群聊历史中与问题最相关的消息"
```

- [ ] **Step 2: `common/__init__.py` 导出**

import 与 `__all__` 各加两个名字：
```python
from .prompts import (
    DEFAULT_PERSONA_PROMPT,
    MEMORY_TOOL_HINT,
    RETRIEVAL_TASK,
    ROUTER_PROMPT,
    SUMMARY_PROMPT,
    VISION_PROMPT,
)

__all__ = [
    "BotConfig",
    "DEFAULT_PERSONA_PROMPT",
    "MEMORY_TOOL_HINT",
    "RETRIEVAL_TASK",
    "ROUTER_PROMPT",
    "SUMMARY_PROMPT",
    "VISION_PROMPT",
]
```

- [ ] **Step 3: 两个服务文件改从 common import**

`bot/core/vision/service.py`：
- 删除 `VISION_PROMPT = "请用中文简要描述这张图片的内容。"` 行（连同其上方注释）
- import 区加 `from common import VISION_PROMPT`

`bot/core/rag/embedder.py`：
- 删除 `RETRIEVAL_TASK = "检索群聊历史中与问题最相关的消息"` 行（连同其上方注释）
- `from common import BotConfig` 改为 `from common import BotConfig, RETRIEVAL_TASK`

- [ ] **Step 4: `tests/test_vision_service.py` import 改源**

`from bot.core.vision.service import VISION_PROMPT, _MAX_IMAGE_BYTES, VisionService` 改为：
```python
from bot.core.vision.service import _MAX_IMAGE_BYTES, VisionService
from common import VISION_PROMPT
```

- [ ] **Step 5: 定向回归 + 冒烟 import**

Run: `uv run pytest tests/test_vision_service.py tests/test_rag_service.py tests/test_embed_cache.py -q`
Expected: 全 PASS（test_vision_service 断言 `payload["prompt"] == VISION_PROMPT` 证明值不变）
Run: `uv run python -c "from common import VISION_PROMPT, RETRIEVAL_TASK; from bot.core.vision.service import VisionService; from bot.core.rag.embedder import EmbeddingService; print(VISION_PROMPT, RETRIEVAL_TASK)"`
Expected: 正常打印两常量，无 ImportError

- [ ] **Step 6: CLAUDE.md 同步**

`common/` 树 `prompts.py` 行改为：
```
  prompts.py                 #   DEFAULT_PERSONA_PROMPT, ROUTER_PROMPT, SUMMARY_PROMPT, MEMORY_TOOL_HINT, VISION_PROMPT, RETRIEVAL_TASK
```

- [ ] **Step 7: Commit**

```bash
git add common/prompts.py common/__init__.py bot/core/vision/service.py bot/core/rag/embedder.py tests/test_vision_service.py CLAUDE.md
git commit -m "refactor: VISION_PROMPT/RETRIEVAL_TASK 归位 common/prompts.py（单一事实来源）"
```

---
---

### Task 4: `[图片]` 占位符单一来源 + `content_kind` 绑枚举

**Files:**
- Modify: `bot/core/utils/content_parser.py`
- Modify: `bot/core/utils/__init__.py`
- Modify: `bot/core/nodes/action_node/describe_image.py`
- Modify: `bot/core/nodes/action_node/index_turn.py`

**Interfaces:**
- Consumes: `bot.core.utils.IMAGE_PLACEHOLDER`（Task 本步产出）、`bot.core.utils.MessageKind`（门面已有）
- Produces: 无新导出

- [ ] **Step 1: `content_parser.py` 导出 `IMAGE_PLACEHOLDER`**

`_PLACEHOLDERS` 字典定义之后加一行：
```python
# [图片] 占位符单一来源（describe_image 原位替换引用，避免魔数重复）
IMAGE_PLACEHOLDER = _PLACEHOLDERS["img"]
```

- [ ] **Step 2: 门面 `bot/core/utils/__init__.py` 导出**

`from bot.core.utils.content_parser import ( ... )` 块加 `IMAGE_PLACEHOLDER`；`__all__` 在 `"Attachment"` 之后加 `"IMAGE_PLACEHOLDER"`：
```python
from bot.core.utils.content_parser import (
    IMAGE_PLACEHOLDER,
    classify_content,
    clean_text,
    parse_attachments,
    parse_content,
    to_llm_text,
)
```

- [ ] **Step 3: `describe_image.py` 引用单一来源**

`from bot.core.vision.service import VisionService` 行上方加：
```python
from bot.core.utils import IMAGE_PLACEHOLDER
```
`replace_placeholders` 内 `marker = "[图片]"` 改为 `marker = IMAGE_PLACEHOLDER`。
（`f"[图片：{desc}]"` 变体与 docstring 保持字面量——该变体仅本节点产出，无其他代码消费。）

- [ ] **Step 4: `index_turn.py` 魔法字符串绑枚举**

`from bot.core.utils import clean_text` 改为 `from bot.core.utils import MessageKind, clean_text`；
`if state.get("content_kind") == "image" and vision_desc:` 改为：
```python
    if state.get("content_kind") == MessageKind.IMAGE.value and vision_desc:
```

- [ ] **Step 5: 定向回归**

Run: `uv run pytest tests/test_describe_image.py tests/test_handler_media.py tests/test_content_parser.py tests/test_object_content.py -q`
Expected: 全 PASS（test_describe_image 证明占位符替换行为不变；test_handler_media 覆盖 image 轮索引；test_content_parser / test_object_content 证明门面导出不破坏）

- [ ] **Step 6: 全量回归**

Run: `uv run pytest tests/ -q`
Expected: 全 PASS（基线 103 + 新增 test_routing 14 + test_context 3 = 120 passed, 1 skipped）

- [ ] **Step 7: Commit**

```bash
git add bot/core/utils/content_parser.py bot/core/utils/__init__.py bot/core/nodes/action_node/describe_image.py bot/core/nodes/action_node/index_turn.py
git commit -m "refactor: [图片] 占位符单一来源 + content_kind 绑定 MessageKind 枚举"
```
