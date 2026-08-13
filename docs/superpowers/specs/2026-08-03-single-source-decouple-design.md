# 单一事实来源解耦：路由判定 / 摘要层 / 提示词 / 占位符 / 魔法字符串

**日期**：2026-08-03
**状态**：已获用户确认（五项目全部推进）

## Context

`content_parser` 解耦完成后，依赖方向已干净（`object/` 只 import langchain/pydantic/标准库，从不反向引用 `bot/` 或 `common/`）。通读全项目后发现剩余的耦合**全部是同一类**：单一事实来源被复制到多处，两处必须同步修改。

逐一列出的五处（按风险/价值排序）：

1. **路由判定表两处重复**：`bot/core/nodes/action_node/detect_intent.py` 计算 `should_respond` + `add_to_context`，`bot/core/graph.py` 的 `_route_after_detect` 用同样的 `content_kind == "text"` 再推导一次路径。CLAUDE.md Gotchas 明确警告"两处逻辑需同步"。
2. **摘要层 SystemMessage 重复**：`call_llm.py:36` 与 `utils/context.py:38` 出现同一句 `f"之前的对话摘要：\n{summary}"`；`estimate_context_tokens` 镜像 call_llm 结构做估算，格式改一处漏一处会**静默偏离**。
3. **提示词散落服务文件**：`common/__init__.py` docstring 声明 common 是 prompt 模板单一事实来源，但 `VISION_PROMPT`（vision/service.py:20）与 `RETRIEVAL_TASK`（rag/embedder.py:22）在外。
4. **`[图片]` 占位符双重定义**：`content_parser._PLACEHOLDERS["img"]` 与 `describe_image.py:16` 的 `marker = "[图片]"` 各一份，改一处另一处静默失配。
5. **`content_kind` 魔法字符串未绑定枚举**：`index_turn.py:26` 等处的 `"text"`/`"image"`/`("file","audio","video")` 未用 `MessageKind.X.value`。

**目标**：把五处收敛为单一事实来源；#1 是唯一行为改动（有集成测试 + 新纯函数测试双保险），#2~#5 零行为风险。

## 决策

1. **路由判定表放 `bot/core/utils/routing.py`**（纯逻辑叶模块），不放 `object/bot/`——`object/` 层是协议数据对象，且无任何 `object/` 模块需要引用该函数（`BotState` 只存 `should_respond` 结果）；依赖方向仍为 `bot.core → object`，无环。
2. **`route_after_detect` 返回 `str | None`**，`None` 表示 END 由 `graph.py` 映射——**utils 不 import langgraph**。
3. **摘要层构建函数放 `utils/context.py`**，与 `estimate_context_tokens` 同文件，token 估算与实际注入共用同一构造。
4. **`VISION_PROMPT` / `RETRIEVAL_TASK` 迁入 `common/prompts.py`**，`common/__init__.py` 导出，服务文件从 common import。
5. **占位符 `IMAGE_PLACEHOLDER` 由 content_parser 导出**（经门面 `bot.core.utils.__init__`），describe_image 引用；`[图片：描述]` 变体仅 describe_image 一处产出（已 grep 确认无其他消费），保持字面量。

## 改动清单

### 1. 新增 `bot/core/utils/routing.py`

```python
"""确定性回复判定（LLM router 架空后的唯一判定表）。

detect_intent 与 graph._route_after_detect 共同消费，消除双处同步。
"""
from object.bot.content import MessageKind
from object.satori import ChannelType

# 永不回复的媒体类型（file/audio/video，即使私聊/@）
NON_REPLY_KINDS = frozenset({MessageKind.FILE.value, MessageKind.AUDIO.value, MessageKind.VIDEO.value})


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

### 2. 修改 `bot/core/nodes/action_node/detect_intent.py`

- 判定块改为调用 `routing.decide_reply` / `routing.keep_in_context`（返回 dict 不变）：
  ```python
  channel_type = state.get("channel_type", 0)
  bot_id = state.get("bot_id", "")
  raw_content = state.get("raw_content", "")
  content_kind = state.get("content_kind", "")
  should_respond = decide_reply(channel_type, content_kind, bot_id, raw_content)
  # ... HumanMessage 构建不变 ...
  add_to_context = keep_in_context(should_respond, content_kind)
  ```
- 判定块的 DIRECT 逻辑移入 `decide_reply` 后，**`from object.satori import ChannelType` 仍保留**——line 59 的 `is_group = channel_type != ChannelType.DIRECT`（群聊消息加发送者 name 到 HumanMessage）继续使用它。

### 3. 修改 `bot/core/graph.py`

- `_route_after_detect` 改为委托：
  ```python
  from bot.core.utils.routing import route_after_detect

  def _route_after_detect(state: BotState) -> str:
      """Deterministic 3-way route（判定表见 bot.core.utils.routing）。"""
      return route_after_detect(
          state.get("should_respond", False),
          state.get("content_kind", ""),
      ) or END
  ```

### 4. 新增 `bot/core/utils/context.py` 共享构建函数

```python
def build_system_messages(persona: str, summary: str = "") -> list[SystemMessage]:
    """构建 call_llm 的前两层 SystemMessage；estimate_context_tokens 复用保证估算一致。"""
    msgs = [SystemMessage(content=persona)] if persona.strip() else []
    if summary.strip():
        msgs.append(SystemMessage(content=f"之前的对话摘要：\n{summary}"))
    return msgs
```

- `estimate_context_tokens` 内部改用该函数（Layer 0 + Layer 1），Layer 2..N 仍 `all_msgs.extend(messages)`。
- `call_llm.py`：`system_msgs = build_system_messages(persona, summary)`，再按需 append `MEMORY_TOOL_HINT`。

### 5. 提示词归位 `common/prompts.py`

- 从 `bot/core/vision/service.py:20` 迁入 `VISION_PROMPT`；从 `bot/core/rag/embedder.py:22` 迁入 `RETRIEVAL_TASK`。
- `common/__init__.py` 的 `__all__` 加两个名字。
- `vision/service.py` / `rag/embedder.py` 改为 `from common import ...`。
- `tests/test_vision_service.py:8` 的 `VISION_PROMPT` import 改从 common 拉。

### 6. 占位符单一来源

- `bot/core/utils/content_parser.py`：`IMAGE_PLACEHOLDER = _PLACEHOLDERS["img"]`（放在 `_PLACEHOLDERS` 定义之后）。
- 门面 `bot/core/utils/__init__.py`：`__all__` 与 import 加 `IMAGE_PLACEHOLDER`。
- `describe_image.py`：`marker = "[图片]"` → `from bot.core.utils import IMAGE_PLACEHOLDER`，`marker = IMAGE_PLACEHOLDER`；`[图片：描述]` 变体保持字面量。

### 7. 魔法字符串绑枚举

- `bot/core/nodes/action_node/index_turn.py:26`：`content_kind == "image"` → `content_kind == MessageKind.IMAGE.value`（加 `from object.bot.content import MessageKind`）。
- `detect_intent` / `graph` 的 `"text"` / `("file","audio","video")` 已由 #1 routing.py 用 `MessageKind.X.value` 取代。

### 8. 新增 `tests/test_routing.py` + 文档

- `tests/test_routing.py`（新建）锁定完整判定表：
  - `decide_reply`：file/audio/video 恒 False（即使 DIRECT/带@）；DIRECT text/image True；群聊@ True；群聊非@ False
  - `keep_in_context`：非回复媒体 False；非回复 text True；回复轮 True
  - `route_after_detect`：should_respond → "describe_image"；非回复 text → "summarize"；非回复媒体 → None
- `CLAUDE.md`：
  - Gotchas 删除"两处逻辑需同步"，改为指向 `bot/core/utils/routing.py` 单一事实来源
  - `utils/` 树加 `routing.py` 一行

## 测试

- 现有 `tests/test_detect_intent.py` / `test_graph.py` / `test_handler_media.py` / `test_call_llm_node.py` **不改**（行为兼容的集成护栏）
- 新增 `tests/test_routing.py` 锁定判定表
- `tests/test_vision_service.py` 仅改 import 来源，断言不变
- 目标：`uv run pytest tests/ -q` 全量通过（当前基线 103 passed, 1 skipped）

## 不改动

- `BotState` 字段与 checkpoint 格式、图接线顺序、handler 初始 state、`bot/core/nodes/llm_node/router.py` 死代码
- 任何函数签名 / 行为：`detect_intent` 返回 dict、`estimate_context_tokens` 返回 int、`replace_placeholders` 行为均不变

## 风险

- **#1 行为改动**：由既有集成测试（test_detect_intent / test_graph / test_handler_media）+ 新纯函数测试双保险；判定表原样搬移，无行为变化预期
- **utils 引入 satori/object 依赖**：`routing.py` import `object.satori.ChannelType`（纯枚举）与 `object.bot.content`，均为正向方向，无环
- **门面导出集合变化**：`bot.core.utils.__init__` 仅**新增** `IMAGE_PLACEHOLDER`，既有的 10 个名字不变，零破坏

## 验证

```
uv run pytest tests/ -q       # 全量回归
```
