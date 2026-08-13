# @ 提及注入实施计划（mentions map + llm_text 渲染 + 混合回复判定）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 @ 提及进入 `ParsedContent.mentions`（顶层 `{昵称: id}` hashmap）、`to_llm_text` 渲染 at（`@昵称(id)`/`所有成员`）供 LLM 感知，回复判定从 `raw_content` 子串改为按顶层提及集合混合判定（id 为主、昵称兜底）。

**Architecture:** 两条独立路径互不喂数据——`to_llm_text` 只做字符串渲染（at→`@昵称(id)`，全量含引用/转发内）；`parse_mentions` 先经 `_top_level_text`（quote/message 子树深度剥离）再匹配 `<at>`，只收顶层提及建 `{昵称: id}` map。`decide_reply` 改消费 `mentions` 集合：`bot_id in 提及id or bot_name in 提及昵称`。判定表仍在 `routing.py` 单一来源。

**Tech Stack:** Python 3.12, pytest, uv。

## Global Constraints

- 被改的源文件：`object/bot/content.py`、`bot/core/utils/content_parser.py`、`bot/core/utils/routing.py`、`bot/core/utils/__init__.py`、`object/bot/state.py`、`bot/handler.py`、`bot/core/nodes/action_node/detect_intent.py`。其余源码（`graph.py`、`describe_image.py`、`index_turn.py`、`router.py`、`common/prompts.py`）**一律不动**。
- `MessageKind`/`Attachment` 结构、`parse_attachments`/`classify_content`/`clean_text` 签名、`_TAG_RE`/`_COMMENT_RE`/`_LINK_RE`/`_MEDIA_TAG_RE`/`_ATTR_RE`/`_PLACEHOLDERS`/`IMAGE_PLACEHOLDER` 定义**不变**。
- `to_llm_text` 管道顺序固定：**注释剥除 → 媒体占位符 → @渲染 → 链接渲染 → 通用标签剥除**。
- 顶层剥离用 `_top_level_text`（quote/message 深度计数）；`parse_mentions` 只认顶层 at；`to_llm_text` 渲染全部 at（含引用/转发内）。
- `_AT_TAG_RE` 仅用于提取/渲染；标签剥离仍走 `_TAG_RE`（单一事实来源）。
- 现有 `tests/test_content_parser.py` 31 个用例中 3 个 at 断言按新行为更新（`test_to_llm_text_strips_at_keeps_text` 改名、`test_to_llm_text_quote_keeps_quoted_text`、`test_parse_content_combines_fields`），其余 28 个**不改**。
- 验证命令：`uv run pytest tests/ -q` 全量通过（当前基线 139 passed, 1 skipped；完成后预计 153 passed, 1 skipped）。
- `docs/superpowers/` 为 git-ignored：设计 spec（`2026-08-03-at-mention-injection-design.md`）不入库，计划内无需 commit 它。

---
---

### Task 1: parser 层 — mentions 提取 + at 渲染

**Files:**
- Modify: `object/bot/content.py`（`ParsedContent` 加 `mentions` 字段）
- Modify: `bot/core/utils/content_parser.py`（正则区 + 三个新函数 + `to_llm_text`/`parse_content` 管道 + docstring）
- Modify: `bot/core/utils/__init__.py`（导出 `parse_mentions`）
- Test: `tests/test_content_parser.py`

**Interfaces:**
- Consumes: 无新依赖（纯 stdlib `re`/`html`）；复用 `_parse_tag_attrs`（单/双引号版）。
- Produces: `parse_mentions(content) -> dict[str, str]`（顶层 `{昵称: id}`）、`ParsedContent.mentions` 字段、`to_llm_text` 的 at 渲染新行为。Task 2 消费 `parsed.mentions`。

- [ ] **Step 1: 写失败测试（更新 3 个既有断言 + 追加 10 个新用例）**

`tests/test_content_parser.py` 顶部 import 加 `parse_mentions`：

```python
from bot.core.utils import (
    Attachment,
    MessageKind,
    classify_content,
    clean_text,
    parse_attachments,
    parse_content,
    parse_mentions,
    to_llm_text,
)
```

既有用例更新（3 处，替换原函数体）：

```python
def test_to_llm_text_renders_at_keeps_text():
    assert to_llm_text(f"{AT} 你好") == "@Bot(bot1) 你好"


def test_to_llm_text_quote_keeps_quoted_text():
    assert to_llm_text('<quote><at id="u1"/>原消息</quote> 回复') == "@u1原消息 回复"
```

`test_parse_content_combines_fields` 函数体改为：

```python
def test_parse_content_combines_fields():
    parsed = parse_content(f"{AT} 看看这张 {IMG}")
    assert parsed.kind == MessageKind.IMAGE
    assert len(parsed.attachments) == 1
    assert isinstance(parsed.attachments[0], Attachment)
    assert parsed.clean_text == "看看这张"
    assert parsed.llm_text == "@Bot(bot1) 看看这张 [图片]"
    assert parsed.mentions == {"Bot": "bot1"}
    assert parsed.has_text is True
    assert parsed.has_media is True
```

末尾追加 10 个新用例：

```python
def test_parse_mentions_collects_names():
    content = '<at id="10001" name="小助手"/><at id="10002" name="张三"/> 大家'
    assert parse_mentions(content) == {"小助手": "10001", "张三": "10002"}


def test_parse_mentions_top_level_only_quote_excluded():
    content = '<quote><at id="10001" name="小助手"/>原消息</quote><at id="10002" name="张三"/>怎么看'
    assert parse_mentions(content) == {"张三": "10002"}


def test_parse_mentions_forward_message_excluded():
    content = '<message><author id="u1" name="张三"/><at id="10001" name="小助手"/>转发内容</message>'
    assert parse_mentions(content) == {}


def test_parse_mentions_nested_message_keeps_top_level():
    content = ('<message forward><message><at id="10001" name="小助手"/>内层</message></message>'
               '<at id="10002" name="张三"/>外层')
    assert parse_mentions(content) == {"张三": "10002"}


def test_parse_mentions_skips_type_all():
    assert parse_mentions('<at type="all"/> 早上好') == {}
    assert parse_mentions('<at type="here"/>') == {}


def test_parse_mentions_id_only_fallback():
    assert parse_mentions('<at id="10001"/>') == {"10001": "10001"}


def test_parse_mentions_empty_no_at():
    assert parse_mentions("纯文本") == {}


def test_to_llm_text_at_without_name_renders_id():
    assert to_llm_text('<at id="u1"/> 你好') == "@u1 你好"


def test_to_llm_text_at_type_all_renders_all_members():
    assert to_llm_text('<at type="all"/> 早上好') == "所有成员 早上好"


def test_to_llm_text_at_type_here_renders_online_members():
    assert to_llm_text('<at type="here"/>') == "在线成员"
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_content_parser.py -q`
Expected: 41 collected，失败点——
- import `parse_mentions` → ImportError（尚未导出）
- 3 个既有更新用例 FAIL（at 仍被剥除：`"你好"` ≠ `"@Bot(bot1) 你好"`、`"原消息 回复"` ≠ `"@u1原消息 回复"`、`llm_text`/`mentions` 不匹配）
- 10 个新用例 FAIL（`parse_mentions` 不存在；渲染未实现）
- 其余 28 个既有用例全 PASS

- [ ] **Step 3: 实现 parser 层**

`object/bot/content.py` `ParsedContent` 追加字段（末尾，带默认值）：

```python
@dataclass
class ParsedContent:
    kind: MessageKind       # 主类型：首个媒体标签决定
    attachments: list[Attachment] = field(default_factory=list)
    clean_text: str = ""    # 剥全部标签、unescape、折叠空白（RAG 用）
    llm_text: str = ""      # 媒体→占位符、@→@昵称(id)/所有成员、剥其余（LLM 用）
    has_text: bool = False
    mentions: dict[str, str] = field(default_factory=dict)  # 顶层 @ 提及 {昵称: id}
```

`bot/core/utils/content_parser.py` 正则区（`_ATTR_RE` 行之后）追加：

```python
_AT_TAG_RE = re.compile(r"<at\b([^>]*?)/?>", re.IGNORECASE)               # 提取/渲染 at
_CONTAINER_TAG_RE = re.compile(r"</?(quote|message)\b[^>]*/?>", re.IGNORECASE)
```

`_render_link` 之后追加 `_render_at`：

```python
def _render_at(m: re.Match) -> str:
    """at 渲染：@昵称(id)/@id；type=all→所有成员、here→在线成员。"""
    attrs = _parse_tag_attrs(m.group(1))
    at_type = attrs.get("type", "")
    if at_type == "all":
        return "所有成员"
    if at_type == "here":
        return "在线成员"
    uid = attrs.get("id", "")
    if not uid:
        return ""
    name = attrs.get("name") or uid
    return f"@{name}({uid})"
```

`parse_attachments` 之后追加 `_top_level_text` 与 `parse_mentions`：

```python
def _top_level_text(content: str) -> str:
    """剥掉 quote/message 子树，返回仅含顶层文本的区域（供 parse_mentions 用）。"""
    out = []
    prev = 0
    depth = 0
    for m in _CONTAINER_TAG_RE.finditer(content):
        tag = m.group(0)
        if depth == 0:
            out.append(content[prev:m.start()])   # 只收 depth==0 的文本
        if tag.startswith("</"):
            depth = max(0, depth - 1)
        elif tag.endswith("/>"):
            pass                                   # 自闭合无子元素
        else:
            depth += 1
        prev = m.end()
    out.append(content[prev:])
    return "".join(out)


def parse_mentions(content: str) -> dict[str, str]:
    """返回顶层 at 提及 {昵称: id}；引用/转发子树不计；type=all/here 跳过。"""
    mentions = {}
    for m in _AT_TAG_RE.finditer(_top_level_text(content)):
        attrs = _parse_tag_attrs(m.group(1))
        if attrs.get("type"):      # type=all/here：非用户提及，不计
            continue
        uid = attrs.get("id", "")
        if not uid:                # 无 id 不算（type-only / 空 at）
            continue
        name = attrs.get("name") or uid   # name 可缺失 → 用 id 当 key
        mentions[name] = uid
    return mentions
```

`to_llm_text` 整体替换为（@渲染插在媒体占位符之后）：

```python
def to_llm_text(content: str) -> str:
    """媒体→占位符、@→@昵称(id)/所有成员、链接→content (href)、其余标签全剥。"""
    text = _COMMENT_RE.sub("", content)
    text = _MEDIA_TAG_RE.sub(lambda m: _PLACEHOLDERS[m.group(1).lower()], text)
    text = _AT_TAG_RE.sub(_render_at, text)
    text = _LINK_RE.sub(_render_link, text)
    text = _TAG_RE.sub("", text)
    return html.unescape(text).strip()
```

`parse_content` 的 `ParsedContent(...)` 调用追加 `mentions=parse_mentions(content),`（放在 `has_text` 行之后）：

```python
    return ParsedContent(
        kind=_kind_from_attachments(attachments),
        attachments=attachments,
        clean_text=clean,
        llm_text=to_llm_text(content),
        has_text=bool(clean.strip()),
        mentions=parse_mentions(content),
    )
```

模块 docstring 第 7-8 行改为：

```python
- ``clean_text``：剥掉全部标签（含闭合/注释），供 RAG 索引用（纯文本）
- ``to_llm_text``：媒体→``[图片]`` 等占位符、@→``@昵称(id)``/``所有成员``、链接→``内容 (href)``、其余标签全剥，供 LLM 用（注：``<a@b.com>``/``<https://...>`` 等非元素尖括号序列同样被剥除）
- ``parse_mentions``：只数顶层 at 提及 ``{昵称: id}``（引用/转发子树不计），供路由判定用
```

`bot/core/utils/__init__.py`：import 块 `content_parser` 那组加 `parse_mentions`，`__all__` 末尾加 `"parse_mentions",`。

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_content_parser.py -q`
Expected: 41 passed

- [ ] **Step 5: 定向回归（解析消费方）**

Run: `uv run pytest tests/test_object_content.py tests/test_handler_media.py tests/test_graph.py tests/test_call_llm_node.py tests/test_describe_image.py -q`
Expected: 全 PASS（这些 fixture 不含 at；index_turn 走 clean_text 不受影响）

- [ ] **Step 6: Commit**

```bash
git add object/bot/content.py bot/core/utils/content_parser.py bot/core/utils/__init__.py tests/test_content_parser.py
git commit -m "feat: parse_mentions 顶层提及 {昵称: id} + to_llm_text 渲染 @昵称(id)/所有成员"
```

---
---

### Task 2: 判定层 — decide_reply 混合判定 + 接线

**Files:**
- Modify: `bot/core/utils/routing.py`（`decide_reply` 签名与逻辑 + docstring）
- Modify: `bot/core/nodes/action_node/detect_intent.py`（传 `bot_name`/`mentions`）
- Modify: `object/bot/state.py`（加 `mentions` 字段 + `llm_text` 注释）
- Modify: `bot/handler.py`（state 注入 `mentions`）
- Modify: `tests/fakes.py`（`make_state` 默认 `mentions`）
- Test: `tests/test_routing.py`（decide_reply 段重写）、`tests/test_detect_intent.py`（3 补 + 1 新）

**Interfaces:**
- Consumes: Task 1 的 `parsed.mentions`（`{昵称: id}`）；state 已有 `bot_name`/`bot_id`。
- Produces: `decide_reply(channel_type, content_kind, bot_id, bot_name, mentions) -> bool`（新签名）；`detect_intent` 读 `state["mentions"]`。Task 3 不消费新接口，仅文档。

- [ ] **Step 1: 写失败测试（重写 decide_reply 段 + detect_intent 补参）**

`tests/test_routing.py` 的 decide_reply 段（原 7 个用例）整体替换为：

```python
# --- decide_reply ---

@pytest.mark.parametrize("kind", ["file", "audio", "video"])
def test_media_never_reply_even_direct_or_mention(kind):
    assert decide_reply(ChannelType.DIRECT, kind, "bot1", "Bot", {}) is False
    assert decide_reply(ChannelType.TEXT, kind, "bot1", "Bot", {"Bot": "bot1"}) is False


def test_direct_text_replies():
    assert decide_reply(ChannelType.DIRECT, "text", "bot1", "Bot", {}) is True


def test_direct_image_replies():
    assert decide_reply(ChannelType.DIRECT, "image", "bot1", "Bot", {}) is True


def test_group_mention_by_id_replies():
    assert decide_reply(ChannelType.TEXT, "text", "bot1", "Bot", {"小助手": "bot1"}) is True


def test_group_mention_by_name_replies():
    # bot_id 不在 map，但 bot_name 命中 → 昵称兜底
    assert decide_reply(ChannelType.TEXT, "text", "bot1", "小助手", {"小助手": "10001"}) is True


def test_group_mention_by_name_with_empty_bot_id():
    assert decide_reply(ChannelType.TEXT, "text", "", "小助手", {"小助手": "10001"}) is True


def test_group_mention_other_user_no_reply():
    assert decide_reply(ChannelType.TEXT, "text", "bot1", "Bot", {"张三": "10002"}) is False


def test_group_without_mention_does_not_reply():
    assert decide_reply(ChannelType.TEXT, "text", "bot1", "Bot", {}) is False


def test_group_image_without_mention_no_reply():
    assert decide_reply(ChannelType.TEXT, "image", "bot1", "Bot", {}) is False


def test_empty_mentions_with_empty_bot_id_no_reply():
    assert decide_reply(ChannelType.TEXT, "text", "", "Bot", {}) is False
```

（keep_in_context / route_after_detect 段不动。）

`tests/test_detect_intent.py` 三个用例补 `mentions` 参数（原 raw_content 里的 at 不再被子串判定，改走 map）：

```python
def test_falls_back_to_mention_strip_when_llm_text_absent():
    state = make_state(
        raw_content='<at id="bot1" name="Bot"/> 你好',
        channel_type=0,
        bot_id="bot1",
        mentions={"Bot": "bot1"},
    )
    result = asyncio.run(detect_intent(state))
    assert result["should_respond"] is True
    assert result["messages"][0].content == "你好"


def test_group_at_mention_responds():
    state = make_state(
        raw_content='<at id="bot1" name="Bot"/> 你好',
        content_kind="text",
        channel_type=0,
        bot_id="bot1",
        mentions={"Bot": "bot1"},
    )
    result = asyncio.run(detect_intent(state))
    assert result["should_respond"] is True


def test_media_never_responds_even_with_mention():
    state = make_state(
        content_kind="file",
        raw_content='<at id="bot1" name="Bot"/><file src="x"/>',
        channel_type=0,
        bot_id="bot1",
        mentions={"Bot": "bot1"},
    )
    result = asyncio.run(detect_intent(state))
    assert result["should_respond"] is False
    assert result["messages"] == []
```

末尾追加一个新用例：

```python
def test_group_name_only_mention_responds_with_empty_bot_id():
    state = make_state(
        llm_text="@小助手(10001) 你好",
        raw_content='<at id="10001" name="小助手"/> 你好',
        content_kind="text",
        channel_type=0,
        bot_id="",
        bot_name="小助手",
        mentions={"小助手": "10001"},
    )
    result = asyncio.run(detect_intent(state))
    assert result["should_respond"] is True
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_routing.py tests/test_detect_intent.py -q`
Expected: 全部 decide_reply 用例 TypeError（签名不匹配，`mentions` 位置参数传给 `raw_content`）；`test_group_name_only_mention_responds_with_empty_bot_id` FAIL（detect_intent 仍按旧签名调用，bot_id 为空 → False）。

- [ ] **Step 3: 实现判定层**

`bot/core/utils/routing.py` `decide_reply` 整体替换（含模块 docstring 更新为"按顶层提及集合判定"）：

```python
def decide_reply(channel_type: int, content_kind: str, bot_id: str, bot_name: str, mentions: dict) -> bool:
    """should_respond：媒体永不回复；私聊回复；群聊按顶层提及判定（id 为主、昵称兜底）。"""
    if content_kind in NON_REPLY_KINDS:
        return False
    if channel_type == ChannelType.DIRECT:
        return True
    mentioned_ids = set(mentions.values())
    mentioned_names = set(mentions)
    return bool(bot_id in mentioned_ids or (bot_name and bot_name in mentioned_names))
```

模块 docstring 第 3-4 行 `raw_content` 子串措辞改为"顶层 at 提及集合（`{昵称: id}`）"。

`bot/core/nodes/action_node/detect_intent.py` 变量区与调用改为：

```python
    channel_type = state.get("channel_type", 0)
    bot_id = state.get("bot_id", "")
    bot_name = state.get("bot_name", "")
    mentions = state.get("mentions", {})
    raw_content = state.get("raw_content", "")
    user_name = state.get("user_name", "")
    content_kind = state.get("content_kind", "")

    # 判定表（decide_reply / keep_in_context）单一来源见 bot.core.utils.routing
    should_respond = decide_reply(channel_type, content_kind, bot_id, bot_name, mentions)
```

模块 docstring 第 5 行 `@-mention` 措辞改为 `top-level @-mention (id + name, see parse_mentions)`。

`object/bot/state.py` 分类注释组末尾（`vision_desc` 行之后）追加：

```python
    mentions: dict[str, str]   # 顶层 @ 提及 {昵称: id}（detect_intent 判定用）
```

`object/bot/state.py` 的 `llm_text` 行注释改为：

```python
    llm_text: str           # media→占位符、@→@昵称(id)/所有成员 — HumanMessage content
```

`bot/handler.py` graph.ainvoke 初始 state 在 `"llm_text": parsed.llm_text,` 行后追加：

```python
                    "mentions": parsed.mentions,
```

`tests/fakes.py` `make_state` 在 `"content_kind": "text",` 行后追加：

```python
        "mentions": {},
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_routing.py tests/test_detect_intent.py -q`
Expected: 全 PASS（routing 19 用例 + detect_intent 15 用例）

- [ ] **Step 5: 定向回归（detect_intent 消费方）**

Run: `uv run pytest tests/test_graph.py tests/test_handler.py tests/test_handler_media.py -q`
Expected: 全 PASS（图/handler 经 `state.get("mentions", {})` 默认空 map，无 at fixture 不受影响）

- [ ] **Step 6: Commit**

```bash
git add bot/core/utils/routing.py bot/core/nodes/action_node/detect_intent.py object/bot/state.py bot/handler.py tests/fakes.py tests/test_routing.py tests/test_detect_intent.py
git commit -m "feat: decide_reply 按顶层提及集合混合判定（id 主 + 昵称兜底），detect_intent 接线"
```

---
---

### Task 3: CLAUDE.md 同步 + 全量回归

**Files:**
- Modify: `CLAUDE.md`（树注释 2 行、数据流 1 行、Gotchas 3 条）

**Interfaces:**
- Consumes: 无（纯文档）；对齐 Task 1/2 落定的行为语义。

- [ ] **Step 1: CLAUDE.md 更新**

树 `content_parser.py` 行（原"媒体→占位符、链接→标题 (url)、其余全剥"）改为：

```
      content_parser.py      #   Satori content 解析逻辑（媒体→占位符、@→@昵称(id)、链接→标题 (url)、其余全剥；类型见 object/bot/content.py）
```

树 `routing.py` 行改为：

```
      routing.py             #   确定性回复判定（decide_reply 按顶层提及集合 id+昵称混合 / keep_in_context / route_after_detect）
```

数据流 `detect_intent` 行（原"text/image 对 DIRECT/@ 回复"）改为：

```
    → detect_intent (action_node)  ← 确定性三路（无 LLM router）：text/image 对 DIRECT/顶层@提及 回复；file/audio/video 永不回复；媒体非回复不入上下文
```

Gotcha `**@-mention format**` 整条替换为：

```
- **@-mention format**: LLOneBot/Satori uses XML `<at id="QQ号" name="昵称"/>`, not `@name`。回复判定基于 `parse_mentions` 的**顶层提及集合** `{昵称: id}`（引用/转发子树不计），`detect_intent` 以 `bot_id` 命中为主、`bot_name` 昵称兜底。LLM 输入 `to_llm_text` 把 at 渲染为 `@昵称(id)`（`<at type="all"/>`→`所有成员`、`here`→`在线成员`）；`detect_intent._strip_mention` 仅在 state 无 `llm_text` 时兜底。
```

Gotcha `**Satori 元素适配（content_parser）**` 整条替换为：

```
- **Satori 元素适配（content_parser）**: `to_llm_text` 媒体→占位符、@→`@昵称(id)`/`所有成员`、链接→`标题 (url)`、其余标签（排版/引用/转发/emoji/sharp/注释）全剥保留内部文本；`clean_text` 剥全部标签含闭合与注释。标签剥离仍走 `_TAG_RE` 单一来源；`_AT_TAG_RE` 仅用于 at 的提取（`parse_mentions`）与渲染。
```

Gotcha `**回复判定树（router 已架空，纯确定性）**` 中两处措辞更新：首句"群聊@时回复"改为"群聊**顶层**@时回复（引用/转发内不计）"；末句"`detect_intent` 与 `graph._route_after_detect` 共同消费，不再需要手动同步。"改为"`decide_reply` 按 `mentions`（`{昵称: id}`）以 id 命中为主、昵称兜底，不再子串匹配 raw_content；`detect_intent` 与 `graph._route_after_detect` 共同消费，不再需要手动同步。"

- [ ] **Step 2: 全量回归**

Run: `uv run pytest tests/ -q`
Expected: 153 passed, 1 skipped（基线 139 + 新增 14）

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: @ 提及注入（parse_mentions/渲染/混合判定）树注释与 Gotchas 同步"
```
