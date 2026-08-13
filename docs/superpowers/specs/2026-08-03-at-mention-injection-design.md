# @ 提及注入：mentions map + llm_text 渲染 + 混合回复判定

**日期**：2026-08-03
**状态**：已获用户确认（顶层 at 才计数；llm_text 全量渲染 at；判定 id 为主、昵称兜底）

## Context

当前 `content_parser.py` 把 `<at>` 标签在 `to_llm_text` 里**整体剥除**：LLM 收到的 HumanMessage 完全不知道谁被@了，也不感知"自己是被@叫出来的"；回复判定靠 `decide_reply` 对 `raw_content` 做 `<at id="{bot_id}"` 子串匹配——该子串匹配会命中**引用（quote）内部**的 at（引用一条曾@bot 的消息也触发回复），且完全依赖 `bot_id` 一个通道。

用户提出四点改进，本 spec 落地前三点，第四点调整为混合判定：

1. **`ParsedContent` 新增 `mentions` hashmap**——收集消息中被@的用户昵称（`{昵称: id}`），供路由判定。
2. **`to_llm_text` 渲染 at**——`<at id name/>` → `@昵称(id)`，LLM 可见谁被@；`<at type="all"/>` → `所有成员`。
3. **顶层 at 才计数**（用户拍板）——引用（quote）/转发（message）子树内的 at **不**进 `mentions`。
4. **回复判定**：id 为主、昵称兜底（用户确认调整）——`bot_id in 提及id ∪ bot_name in 提及昵称`。

## 决策

1. **两条独立路径，互不喂数据**：`to_llm_text` 只做字符串渲染（at→`@名字(id)`），不写任何 map；`parse_mentions` 才是建 hashmap 的地方，且**在匹配 at 之前先剥离 quote/message 子树**（`_top_level_text`），引用/转发内的 at 在它眼里不存在——结构上杜绝"引用 at 进 map"。
2. **顶层剥离用深度计数**（`_CONTAINER_TAG_RE` 扫描 + `depth` 计数），不用非贪婪成对正则——合并转发嵌套 `<message>` 也能正确处理，不误剥顶层 at。
3. **`to_llm_text` 全量渲染 at（含引用/转发内）**——LLM 读的是整条消息转录，@谁对理解有用；只有 `parse_mentions` 限制顶层。两条输出语义不交叉（`llm_text` 渲染后已无 `<at>` 标签，误喂回 `parse_mentions` 也匹配不到）。
4. **`clean_text` 不动**（全标签剥除）——RAG 索引向量空间保持纯净，不带 @ 噪音。
5. **渲染格式**：`@昵称(id)`；无 `name` → `@id`；`type="all"` → `所有成员`；`type="here"` → `在线成员`。
6. **`type="all"/"here"` 不进 `mentions`**——非用户身份，且 @全体不应触发回复（与现状一致）。
7. **`decide_reply` 判定从 `raw_content` 子串改为 `mentions` 集合**——id 为主（QQ 号不可变、唯一）、昵称兜底（`bot_id` 为空 / 改昵称失配时仍可识别）。判定表仍只在 `routing.py` 单一来源。
8. **新增 `_AT_TAG_RE` 仅用于提取/渲染**——标签剥离仍走 `_TAG_RE`（单一事实来源不变），二者用途不同，不冲突。

## 改动清单

### 1. `object/bot/content.py` — ParsedContent 加字段

```python
@dataclass
class ParsedContent:
    kind: MessageKind
    attachments: list[Attachment] = field(default_factory=list)
    clean_text: str = ""
    llm_text: str = ""
    has_text: bool = False
    mentions: dict[str, str] = field(default_factory=dict)  # 顶层 @ 提及 {昵称: id}
```

（现有 `MessageKind`/`Attachment` 结构零改动；`ParsedContent` 仅追加带默认值字段，全部 kwargs 构造，向后兼容。）

### 2. `bot/core/utils/content_parser.py` — 提取与渲染

正则区新增（`_TAG_RE`/`_COMMENT_RE`/`_LINK_RE`/`_MEDIA_TAG_RE`/`_ATTR_RE` 保持原样）：

```python
_AT_TAG_RE = re.compile(r"<at\b([^>]*?)/?>", re.IGNORECASE)               # 提取/渲染 at
_CONTAINER_TAG_RE = re.compile(r"</?(quote|message)\b[^>]*/?>", re.IGNORECASE)
```

`_top_level_text`（深度扫描剥离 quote/message 子树）：

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
```

`parse_mentions`（建 hashmap）：

```python
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

`_render_at`（渲染）：

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

`to_llm_text` 管道（at 渲染插入媒体占位符之后、链接渲染之前；渲染结果无尖括号，不会被 `_TAG_RE` 剥掉）：

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

`parse_content` 增补 mentions：

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

模块 docstring：`to_llm_text` 描述改为"媒体→占位符、@→`@昵称(id)`/`所有成员`、链接→`内容 (href)`、其余标签全剥"。

### 3. `bot/core/utils/routing.py` — 混合判定

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

模块 docstring 更新（`raw_content` 子串 → `mentions` 集合；仅顶层 at）。

### 4. `object/bot/state.py`

```python
mentions: dict[str, str]   # 顶层 @ 提及 {昵称: id}（detect_intent 判定用）
```

`llm_text` 注释改为 `# media→占位符、@→@昵称(id)/所有成员 — HumanMessage content`。

### 5. `bot/handler.py`

`_process` 的 graph.ainvoke 初始 state 增补：

```python
"mentions": parsed.mentions,
```

### 6. `bot/core/nodes/action_node/detect_intent.py`

```python
bot_name = state.get("bot_name", "")
mentions = state.get("mentions", {})
should_respond = decide_reply(channel_type, content_kind, bot_id, bot_name, mentions)
```

更新模块 docstring 与注释（"@-mention detection" 措辞 → 顶层提及集合）。

### 7. `bot/core/utils/__init__.py`

`from bot.core.utils.content_parser import (…, parse_mentions)`；`__all__` 追加 `"parse_mentions"`。

### 8. `tests/fakes.py`

`make_state` 默认值追加 `"mentions": {}`。

## 行为对照

| 输入（群聊） | `mentions` | `llm_text` | `clean_text` | `decide_reply` |
|---|---|---|---|---|
| `<at id="10001" name="小助手"/> 你好` | `{"小助手": "10001"}` | `@小助手(10001) 你好` | `你好` | True（id 命中） |
| `<at id="10001"/> 你好` | `{"10001": "10001"}` | `@10001 你好` | `你好` | True（id 命中） |
| `<at type="all"/> 早上好` | `{}` | `所有成员 早上好` | `早上好` | False（@全体不回复） |
| `<quote><at id="10001" name="小助手"/>原</quote> 回复` | `{}` | `@小助手(10001)原 回复` | `原 回复` | False（引用内不计） |
| `<at id="10002" name="张三"/> 在吗`（bot_id=10001） | `{"张三": "10002"}` | `@张三(10002) 在吗` | `在吗` | False（@别人） |
| 手打 `@小助手`（非标签） | `{}` | 原样透传 | 原样透传 | False（非标签不识别） |

## 测试

**`tests/test_content_parser.py`（3 个既有断言更新 + 约 9 个新增）**：

既有改：
- `test_to_llm_text_strips_at_keeps_text` → 改名 `test_to_llm_text_renders_at_keeps_text`，断言 `to_llm_text(f"{AT} 你好") == "@Bot(bot1) 你好"`（`AT='<at id="bot1" name="Bot"/>'`）。
- `test_to_llm_text_quote_keeps_quoted_text`：`'<quote><at id="u1"/>原消息</quote> 回复'` → `"@u1原消息 回复"`（引用内 at 也渲染）。
- `test_parse_content_combines_fields`：`parsed.llm_text == "@Bot(bot1) 看看这张 [图片]"`；新增 `assert parsed.mentions == {"Bot": "bot1"}`。

新增：
- `parse_mentions` 收集昵称（多 at）；只数顶层（quote 排除）；forward message 排除；type=all 跳过；id-only 回退 key=id；纯文本返回 `{}`。
- `to_llm_text`：无 name → `@id`；`type="all"` → `所有成员`；`type="here"` → `在线成员`。
- `_top_level_text` 嵌套 message（合并转发）不误剥顶层 at。

**`tests/test_routing.py`（decide_reply 6 个既有用例改签名 + 新混合判定用例）**：
- 签名改 `(channel_type, content_kind, bot_id, bot_name, mentions)`；媒体门/私聊/空 mentions 用例改传 `{}`。
- 新增：id 命中回复；name 命中回复（`bot_id` 不在 map）；`bot_id=""` 仅 name 命中回复；@别人不回复。
- `keep_in_context` / `route_after_detect` 用例不动。

**`tests/test_detect_intent.py`（@ 相关用例补 `mentions`）**：
- `test_falls_back_to_mention_strip_when_llm_text_absent` / `test_group_at_mention_responds` / `test_media_never_responds_even_with_mention` 补 `mentions={"Bot": "bot1"}`。
- 新增：群聊仅昵称命中（bot_id 为空）→ True。
- 其余（纯文本/图片私聊）不动。

**回归**：`tests/test_graph.py`、`test_handler*.py`、`test_describe_image.py`、`test_call_llm_node.py`、`test_rag_service.py` 的 fixture 均不含 at，断言不受影响；全量 `uv run pytest tests/ -q` 须通过。

## 不改动

- `clean_text`（全标签剥除）、`describe_image`（`[图片]` 锚点）、`index_turn`/RAG（`clean_text` 消费方）
- `_TAG_RE`/`_COMMENT_RE`/`_LINK_RE`/`_MEDIA_TAG_RE`/`_ATTR_RE`/`_PLACEHOLDERS`/`IMAGE_PLACEHOLDER`
- `MessageKind`/`Attachment` 结构；`parse_attachments`/`classify_content` 签名
- `graph.py`（只消费 `should_respond`/`route_after_detect`）；`router.py`/`ROUTER_PROMPT`（保留未接线）

## 风险

- **判定语义变化**：引用内@不再触发回复（原 `raw_content` 子串会命中）——用户已拍板（只数顶层），测试锁定。
- **昵称为 key 的去重**：同昵称用户后写覆盖——仅影响"@另一个同名用户"场景，id 主通道仍正确；name 仅兜底。
- **未配对 `<quote>`/`<message>`**（规范视为文本）深度不归零 → 后续顶层文本被当子树剥掉、mentions 漏。真实 QQ 始终配对，风险低；配对场景有测试。
- **llm_text 变化**：含 at 消息进 LLM 多 `@昵称(id)`——意图内升级；describe_image 的 `[图片]` 锚点不受影响。
- **`bot_name` 与 QQ 昵称不一致**：name 兜底不命中，但 id 主通道不受影响（只丢兜底能力）。

## 验证

```
uv run pytest tests/test_content_parser.py tests/test_routing.py tests/test_detect_intent.py -q   # 定向
uv run pytest tests/ -q                                                                            # 全量回归
```
