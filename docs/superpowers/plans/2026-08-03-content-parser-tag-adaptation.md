# Satori 元素适配实施计划（content_parser 标签处理收敛）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 收敛 `content_parser.py` 的标签处理为单一规则——媒体→占位符、链接→`标题 (url)`、其余标签（含闭合/注释）一律剥除，消除 `to_llm_text` 的标签泄漏与 `clean_text` 的闭合标签残留，并补属性语法健壮化。

**Architecture:** 三条新正则（`_TAG_RE` 起始/闭合/自闭合、`_COMMENT_RE` 注释、`_LINK_RE` 链接）取代 `_AT_TAG_RE`/`_ALL_TAG_RE`；`to_llm_text` 固定四步管道（注释剥除 → 媒体占位符 → 链接渲染 → 其余标签全剥）；`clean_text` 注释 + 全标签一次剥净；`_ATTR_RE` 支持单/双引号，`Attachment.name` 加 `title` 回退。结构（`Attachment`/`ParsedContent`/`MessageKind`）与门面导出零改动。

**Tech Stack:** Python 3.12, pytest, uv。

## Global Constraints

- 唯一被改的源文件是 `bot/core/utils/content_parser.py`；测试文件 `tests/test_content_parser.py`；文档 `CLAUDE.md`。其余源码（`object/`、`bot/handler.py`、`routing.py`、`describe_image.py`、`index_turn.py`、`utils/__init__.py`）**一律不动**。
- `Attachment`/`ParsedContent`/`MessageKind` 结构、`parse_content` 签名、`bot.core.utils.__init__` 导出集合（10 个既有名字 + `IMAGE_PLACEHOLDER`/`build_system_messages`）**不变**。
- `_MEDIA_TAG_RE` / `_PLACEHOLDERS` / `IMAGE_PLACEHOLDER` 定义**不变**（单一事实来源）。
- 现有 `tests/test_content_parser.py` 16 个用例**不改**（行为兼容集成护栏）。
- `to_llm_text` 管道顺序固定：**注释剥除 → 媒体占位符 → 链接渲染 → 通用标签剥除**（链接内媒体先变占位符、链接先于通用剥除被消费）。
- 验证命令：`uv run pytest tests/ -q` 全量通过（当前基线 124 passed, 1 skipped；完成后预计 136 passed, 1 skipped）。
- `docs/superpowers/` 为 git-ignored：设计 spec（`2026-08-03-content-parser-tag-adaptation-design.md`）不入库，计划内无需 commit 它。

---
---

### Task 1: 标签剥除收敛（行为核心）

**Files:**
- Modify: `bot/core/utils/content_parser.py`
- Test: `tests/test_content_parser.py`
- Modify: `CLAUDE.md`（树注释行——随本任务行为定稿）

**Interfaces:**
- Consumes: 无新依赖（纯 stdlib `re`/`html`）
- Produces: `to_llm_text(content) -> str` 新行为（链接→`标题 (url)`、非媒体标签全剥）、`clean_text(content) -> str` 新行为（含闭合/注释剥除）；内部 `_render_link(m: re.Match) -> str`。Task 2 复用 `_parse_tag_attrs`（本任务仍为双引号版）。

- [ ] **Step 1: 写失败测试（10 个，追加到 `tests/test_content_parser.py` 末尾）**

```python
def test_to_llm_text_link_renders_content_href():
    assert to_llm_text('<a href="https://x.com/a?b=1&amp;c=2">点击</a> 你好') == "点击 (https://x.com/a?b=1&c=2) 你好"


def test_to_llm_text_link_without_href_keeps_inner():
    assert to_llm_text('<a>无链接</a>') == "无链接"


def test_to_llm_text_strips_paired_markup_keeps_text():
    assert to_llm_text('<b>加粗</b>和<i>斜体</i>') == "加粗和斜体"
    assert to_llm_text('<code>code</code>') == "code"


def test_to_llm_text_quote_keeps_quoted_text():
    assert to_llm_text('<quote><at id="u1"/>原消息</quote> 回复') == "原消息 回复"


def test_to_llm_text_strips_emoji_sharp_br():
    assert to_llm_text('<emoji name="smile"/> 哈哈') == "哈哈"
    assert to_llm_text('<sharp id="c1"/> 频道') == "频道"
    assert to_llm_text('第一行<br/>第二行') == "第一行第二行"


def test_to_llm_text_forward_message_keeps_inner():
    assert to_llm_text('<message><author id="u1" name="张三"/>转发内容</message>') == "转发内容"


def test_to_llm_text_strips_comment():
    assert to_llm_text('前<!-- 注释 -->后') == "前后"


def test_clean_text_strips_paired_link_no_closing_leak():
    assert clean_text('<a href="https://x.com">点击</a> 你好') == "点击 你好"


def test_clean_text_strips_quote_and_at():
    assert clean_text('<quote><at id="u1"/>原消息</quote> 回复') == "原消息 回复"


def test_clean_text_strips_comments():
    assert clean_text('前<!-- 注释 -->后') == "前后"
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_content_parser.py -q`
Expected: 26 collected，10 个新用例 FAIL——
- 链接/quote/排版/emoji/sharp/br/转发/注释：`to_llm_text` 返回原始 XML（标签泄漏）
- `clean_text` 链接/quote：残留 `</a>` / `</quote>`（闭合标签不匹配）
- 既有 16 用例全 PASS（行为兼容护栏）

- [ ] **Step 3: 实现正则与两条清洗函数**

`bot/core/utils/content_parser.py` 正则区（原第 17-20 行：`_MEDIA_TAG_RE` / `_AT_TAG_RE` / `_ALL_TAG_RE` / `_ATTR_RE`）调整为——**删除 `_AT_TAG_RE` 与 `_ALL_TAG_RE`，新增 `_TAG_RE` / `_COMMENT_RE` / `_LINK_RE`，`_MEDIA_TAG_RE` 与 `_ATTR_RE` 保持原样**。结果块：

```python
_MEDIA_TAG_RE = re.compile(r"<(img|file|audio|video)\b([^>]*?)/?>", re.IGNORECASE)
_TAG_RE = re.compile(r"</?[a-z]+\b[^>]*?/?>", re.IGNORECASE)   # 起始/闭合/自闭合
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)             # 注释（message.md 语法）
_LINK_RE = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.IGNORECASE | re.DOTALL)
_ATTR_RE = re.compile(r'([a-zA-Z0-9_-]+)\s*=\s*"([^"]*)"')     # 双引号版（Task 2 升级为单/双引号）
```

模块 docstring 第 6-8 行改为：

```python
- ``clean_text``：剥掉全部标签（含闭合/注释），供 RAG 索引用（纯文本）
- ``to_llm_text``：媒体→``[图片]`` 等占位符、链接→``内容 (href)``、其余标签全剥，供 LLM 用
```

`clean_text` 整体替换为：

```python
def clean_text(content: str) -> str:
    """剥掉全部元素标签与注释（含闭合标签），unescape 并折叠空白。"""
    text = _COMMENT_RE.sub("", content)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()
```

`to_llm_text` 整体替换为（含新增 `_render_link`）：

```python
def _render_link(m: re.Match) -> str:
    """链接按 Satori 无平台支持时的建议渲染 content (href)。"""
    inner = m.group(2).strip()
    url = _parse_tag_attrs(m.group(1)).get("href", "")
    if not url:
        return inner
    return f"{inner} ({url})" if inner else url


def to_llm_text(content: str) -> str:
    """媒体→占位符、链接→content (href)、其余标签（at/排版/引用/转发…）全剥。"""
    text = _COMMENT_RE.sub("", content)
    text = _MEDIA_TAG_RE.sub(lambda m: _PLACEHOLDERS[m.group(1).lower()], text)
    text = _LINK_RE.sub(_render_link, text)
    text = _TAG_RE.sub("", text)
    return html.unescape(text).strip()
```

（其余函数 `parse_attachments`/`_kind_from_attachments`/`classify_content`/`parse_content` 不动。）

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_content_parser.py -q`
Expected: 26 passed

- [ ] **Step 5: 定向回归（解析消费方）**

Run: `uv run pytest tests/test_content_parser.py tests/test_object_content.py tests/test_detect_intent.py tests/test_handler_media.py tests/test_call_llm_node.py tests/test_graph.py -q`
Expected: 全 PASS（handler/describe_image/index_turn/graph 证明下游不受影响）

- [ ] **Step 6: CLAUDE.md 树注释行同步**

`utils/` 树第 36 行改为（对齐到列 30）：

```
      content_parser.py      #   Satori content 解析逻辑（媒体→占位符、链接→标题 (url)、其余全剥；类型见 object/bot/content.py）
```

- [ ] **Step 7: Commit**

```bash
git add bot/core/utils/content_parser.py tests/test_content_parser.py CLAUDE.md
git commit -m "fix: to_llm_text 全量标签剥除 + 链接 content (href)，clean_text 修闭合标签残留"
```

---
---

### Task 2: 属性健壮化（单引号 + title 回退）+ 全量回归

**Files:**
- Modify: `bot/core/utils/content_parser.py`
- Test: `tests/test_content_parser.py`
- Modify: `CLAUDE.md`（新增 gotcha）

**Interfaces:**
- Consumes: Task 1 的 `_TAG_RE`/`_COMMENT_RE`/`_LINK_RE`/`_render_link`（本任务不改它们）；Task 1 的 `_parse_tag_attrs`（本任务升级为单/双引号）
- Produces: `_ATTR_RE` 单/双引号、`_parse_tag_attrs` 双引号 group(2)/单引号 group(3)、`parse_attachments` 的 `name`→`title` 回退

- [ ] **Step 1: 写失败测试（2 个，追加到 `tests/test_content_parser.py` 末尾）**

```python
def test_parse_attachments_single_quoted_src():
    assert parse_attachments("<img src='a.png'/>")[0].src == "a.png"


def test_parse_attachments_title_fallback_to_name():
    assert parse_attachments('<file title="报告.pdf" src="b.pdf"/>')[0].name == "报告.pdf"
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_content_parser.py -q`
Expected: 28 collected，2 个新用例 FAIL——单引号 `src` 取不到（`_ATTR_RE` 只认双引号）、`title` 不回退（只读 `name`）

- [ ] **Step 3: 实现属性健壮化**

`_ATTR_RE` 改为单/双引号版：

```python
_ATTR_RE = re.compile(r'([a-zA-Z0-9_-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\')')
```

`_parse_tag_attrs` 整体替换为（双引号取 group(2)、单引号取 group(3)）：

```python
def _parse_tag_attrs(tag_body: str) -> dict:
    attrs = {}
    for m in _ATTR_RE.finditer(tag_body):
        value = m.group(2) if m.group(2) is not None else m.group(3)
        attrs[m.group(1)] = html.unescape(value)
    return attrs
```

`parse_attachments` 的 name 行改为（title 规范回退）：

```python
name=attrs.get("name") or attrs.get("title", ""),
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_content_parser.py -q`
Expected: 28 passed

- [ ] **Step 5: CLAUDE.md 新增 gotcha**

Gotchas 区新增一条（放在 "@-mention format" 之后）：

```
- **Satori 元素适配（content_parser）**: `to_llm_text` 媒体→占位符、链接→`标题 (url)`、其余标签（at/排版/引用/转发/emoji/sharp/注释）全剥保留内部文本；`clean_text` 剥全部标签含闭合与注释。`_AT_TAG_RE` 已并入 `_TAG_RE`（标签剥离单一来源）。
```

- [ ] **Step 6: 全量回归**

Run: `uv run pytest tests/ -q`
Expected: 136 passed, 1 skipped（基线 124 + 新增 12）

- [ ] **Step 7: Commit**

```bash
git add bot/core/utils/content_parser.py tests/test_content_parser.py CLAUDE.md
git commit -m "fix: 属性解析支持单引号 + Attachment.name title 回退"
```
