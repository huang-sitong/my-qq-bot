# Satori 消息元素完整适配：content_parser 标签处理收敛

**日期**：2026-08-03
**状态**：已获用户确认（链接渲染=`标题 (url)`；含属性健壮化）

## Context

对照 `object/bot/content.py` 与 Satori 标准元素清单（`.others/satori_docs/protocol/elements.md` + `message.md`），当前 `bot/core/utils/content_parser.py` 只完整适配了**媒体标签 + at + 纯文本**，其余标准元素在两条清洗路径存在泄漏：

1. **`to_llm_text`（LLM 输入）只认"媒体→占位符 + 剥 at"**，其余标签（`<a>`、`<quote>`、`<b>/<i>/<code>`、`<emoji>`、`<sharp>`、`<br/>`、`<message>`、`<author>`、`<button>`、注释）以**原始 XML 形态泄漏**进 LLM 上下文。
2. **`clean_text`（RAG 索引）的 `_ALL_TAG_RE = <[a-z]+\b[^>]*?/?>` 只匹配起始/自闭合**，成对标签的闭合标签（`</a>`、`</quote>`、`</p>` 等）不匹配 → 索引文本残留 `</a>` 垃圾（QQ 链接 `内容</a>` 是真实高频形态）。
3. **属性语法不完整**（`message.md` 明文）：`_ATTR_RE` 只认双引号 `key="value"`，单引号 `key='value'` 不支持；资源元素文件名规范是 `title`，parser 只读 `name`。

**目标**：标签处理收敛为单一规则——**媒体→占位符；链接按 Satori 规范渲染 `标题 (href)`；其余标签一律剥除（保留内部文本）**。`_AT_TAG_RE` 被通用正则取代（单一事实来源收敛）。`Attachment`/`ParsedContent`/`MessageKind` 结构零改动，下游（routing/describe_image/index_turn/handler）零影响。

## 决策

1. **链接渲染 `标题 (url)`**——elements.md 明确"当平台不支持链接时，建议显示为 `content (href)`"。LLM/RAG 即"不支持链接的平台"；内嵌 URL 使 LLM 可感知链接、RAG 可按 URL 检索。链接是群聊最常见富文本形态，值得完整保留信息。
2. **非媒体标签一律剥除、保留内部文本**——不加 `[链接]/[表情]` 等占位符映射（`describe_image` 只消费 `[图片]` 锚点，其余占位符无下游消费者；内部文本对 LLM 信息量更高）。
3. **`title` 回退 + 单引号属性**——对齐 `elements.md`/`message.md` 明文规范；`_parse_tag_attrs` 被链接渲染复用，顺手受益。
4. **注释剥除**——`message.md` 明文语法，转发等场景可能出现。

## 改动清单（单文件 `bot/core/utils/content_parser.py` + 测试 + CLAUDE.md）

### 1. 正则（第 17-20 行）

```python
_TAG_RE = re.compile(r"</?[a-z]+\b[^>]*?/?>", re.IGNORECASE)   # 起始/闭合/自闭合
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)             # 注释
_LINK_RE = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.IGNORECASE | re.DOTALL)
_ATTR_RE = re.compile(r'([a-zA-Z0-9_-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\')')  # 双/单引号
```

- `_AT_TAG_RE` 删除（`_TAG_RE` 覆盖）；`_MEDIA_TAG_RE`/`_PLACEHOLDERS` 不动。

### 2. `_parse_tag_attrs`（单/双引号取值）

```python
def _parse_tag_attrs(tag_body: str) -> dict:
    attrs = {}
    for m in _ATTR_RE.finditer(tag_body):
        value = m.group(2) if m.group(2) is not None else m.group(3)
        attrs[m.group(1)] = html.unescape(value)
    return attrs
```

### 3. `parse_attachments`（title 规范回退）

`name=attrs.get("name")` → `name=attrs.get("name") or attrs.get("title", "")`。

### 4. `clean_text`（闭合标签 + 注释一次剥净）

```python
def clean_text(content: str) -> str:
    """剥掉全部元素标签与注释（含闭合标签），unescape 并折叠空白。"""
    text = _COMMENT_RE.sub("", content)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()
```

### 5. `to_llm_text`（媒体→占位符 → 链接渲染 → 其余标签全剥）

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

### 6. 模块 docstring（第 1-11 行）

`to_llm_text` 描述从"媒体→占位符、剥 at"更新为"媒体→占位符、链接→content (href)、其余标签全剥"。

## 行为对照

| 输入 | `to_llm_text`（修后） | `clean_text`（修后） |
|---|---|---|
| `<img src="x"/>` | `[图片]`（不变） | ``（不变） |
| `<at id="bot1"/> 你好` | `你好`（不变） | `你好`（不变） |
| `<a href="u">点击</a>` | `点击 (u)` | `点击`（原 `点击</a>`） |
| `<quote>原消息</quote> 回复` | `原消息 回复` | `原消息 回复` |
| `<b>加粗</b>和<i>斜体</i>` | `加粗和斜体` | `加粗和斜体` |
| `<emoji name="x"/> 哈哈` | `哈哈` | `哈哈` |
| `<sharp id="c1"/> 频道` | `频道` | `频道` |
| `第一行<br/>第二行` | `第一行第二行` | `第一行第二行` |
| `<message><author…/>转发</message>` | `转发` | `转发` |
| `前<!-- 注释 -->后` | `前后` | `前后` |
| `<file title="a.pdf" src="x"/>` | `[文件]`（name=title 回退） | `` |

## 测试

现有 `tests/test_content_parser.py` 16 用例**不改**（行为兼容护栏）。新增 ~9 用例：

- 链接渲染：`to_llm_text('<a href="https://x.com">点击</a> 你好') == "点击 (https://x.com) 你好"`；`clean_text` 无 `</a>` 残留
- 链接无 href：`to_llm_text('<a>无链接</a>') == "无链接"`（仅内部文本）
- quote / 加粗斜体 code / emoji / sharp / br / message 转发：to_llm_text 剥标签留文本
- 注释：clean_text 与 to_llm_text 均剥净
- 单引号属性：`parse_attachments("<img src='a.png'/>")[0].src == "a.png"`
- title 回退：`parse_attachments('<file title="报告.pdf" src="b.pdf"/>')[0].name == "报告.pdf"`
- 既有媒体/at/占位符路径不变

## 不改动

- `Attachment`/`ParsedContent`/`MessageKind` 结构、`parse_content` 签名、`bot.core.utils.__init__` 导出集合
- `_MEDIA_TAG_RE`/`_PLACEHOLDERS`、`IMAGE_PLACEHOLDER` 单一来源
- `routing.py`（@判定走 `raw_content`）、`describe_image.py`（`[图片]` 锚点）、`index_turn.py`（`clean_text` 消费方，输出只可能更干净）
- `handler.py` 初始 state 构造

## 风险

- **to_llm_text/clean_text 输出变化**：仅当消息内含非媒体、非 at 标签时变化（纯改进）。现有测试证明媒体/at/纯文本路径零变化。
- **`<a>` 成对形态**：`<a href="x"/>` 自闭合或未配对（message.md 视为文本）会退化为剥除、丢失 href——QQ 实际下发均为成对 `<a>标题</a>`，可接受。
- **顺序依赖**：to_llm_text 必须"媒体替换 → 链接渲染 → 通用剥除"依次执行（链接内媒体先变占位符、链接先于通用剥除被消费）。测试锁定顺序语义。

## 验证

```
uv run pytest tests/test_content_parser.py tests/test_object_content.py -q   # 定向
uv run pytest tests/ -q                                                       # 全量（基线 124 passed, 1 skipped）
```
