# BotState 冗余字段清理设计

日期：2026-08-03
状态：已批准（用户确认维持 TypedDict、不引入 Pydantic、不嵌套）

## 背景

`object/bot/state.py` 的 `BotState`（TypedDict）当前有 20 个字段。逐一核对全部生产/消费路径后，发现 3 个冗余字段与 1 处重复计算；`new_message` 的唯一读者是已从图中摘除的 `router`。

本设计只做"删冗余 + 免重复解析"，不改变任何运行时行为。经与用户确认：**不引入 Pydantic State、不做嵌套结构**——state 全部由 handler/图节点自身写入，无外部校验需求；LangGraph 对 state 所有字段跨轮持久化，嵌套不改变行为却增加迁移成本（分析见 §6）。

## §1 State 模式变更：20 → 18 字段

### 删除（3 个）

| 字段 | 类型 | 删除理由 |
|---|---|---|
| `new_message` | `HumanMessage` | 唯一读者是未接线的 `router`（已改为读 `llm_text`，见 §2）；真正的消息体在 `messages` |
| `raw_content` | `str` | 两个消费者都是冗余路径：① `detect_intent._strip_mention` 兜底在产线是死代码（handler 每轮必注入 `llm_text`）；② `index_turn` 每轮用 `clean_text(raw_content)` 重复解析 |
| `session_id` | `str` | 仅日志用，且 `session_id = f"{thread_id}:{user_id}"` 可随时派生 |

### 新增（1 个）

| 字段 | 类型 | 说明 |
|---|---|---|
| `clean_text` | `str` | handler 里 `parse_content` 已算出却丢弃的清洗文本，现存入 state 供 `index_turn` 直接使用，省掉每轮一次重复正则解析 |

### 变更后完整 schema（18 字段）

```python
messages: Annotated[list[BaseMessage], add_messages]
persona: str
conversation_summary: str   # progressive summary of older messages (dynamic inject)
thread_id: str              # checkpoint isolation key = platform:guild:channel
user_id: str                # 当前消息发送者（记忆工具按用户维度存取）
reply_text: str
should_respond: bool
bot_name: str
tool_rounds: int            # 工具调用轮次计数（call_llm 递增，工具回环上限）
channel_type: int           # ChannelType enum value (0=TEXT, 1=DIRECT)
bot_id: str                 # bot 自身 user ID（@-mention 判定）
user_name: str              # 发送者显示名（群聊归属）
content_kind: str           # MessageKind.value: "text"/"image"/"file"/"audio"/"video"
llm_text: str               # 媒体→占位符、@→@昵称(id)/所有成员 — HumanMessage content
clean_text: str             # 剥全部标签、unescape、折叠空白（RAG 索引用，handler 预计算）
image_srcs: list[str]       # 本轮图片 URL（describe_image 视觉理解用）
vision_desc: str            # 本轮图片描述（RAG 索引；仅 image 轮有效）
mentions: dict[str, str]    # 顶层 @ 提及 {id: 昵称}（detect_intent 判定用）
```

## §2 死代码处理：router 保留为预留节点

用户决定**不删除** `bot/core/nodes/llm_node/router.py` 与 `common/prompts.py` 的 `ROUTER_PROMPT`，作为预留节点保留。为使其在新 schema 下仍自洽，修正两处数据依赖：

- `state['new_message'].content` → `state.get("llm_text", "")`
  （读法改为与当前图一致：detect_intent 先行，`llm_text` 即渲染后的用户消息）
- `state["session_id"]` → `state.get("thread_id", "")`

同时仍删除 `detect_intent._strip_mention`（产线永远走 `llm_text`，兜底是死分支）。

## §3 逐文件改动

| 文件 | 改动 |
|---|---|
| `object/bot/state.py` | 删 `new_message`/`raw_content`/`session_id`；加 `clean_text`；更新 docstring |
| `bot/handler.py` | 删 ainvoke 输入的占位 `new_message` 与 `raw_content`/`session_id`；加 `clean_text: parsed.clean_text`；日志 `session_id` → `thread_id`（保留本地 `raw_content` 变量仅用于日志） |
| `bot/core/nodes/action_node/detect_intent.py` | 删 `_strip_mention` 与 `raw_content` 兜底；`content = state.get("llm_text", "")`；return 删 `new_message` |
| `bot/core/nodes/action_node/index_turn.py` | `content = state.get("clean_text", "")` 替代 `clean_text(state.get("raw_content", ""))`；删 `clean_text` import（保留 `MessageKind`） |
| `bot/core/nodes/llm_node/router.py` | 数据依赖修正（见 §2），仅此两处 |
| `tests/fakes.py` `make_state` | 删 3 字段、加 `clean_text` 默认 |
| `tests/test_detect_intent.py` | 删"无 llm_text 走 raw_content 兜底"用例 |
| `tests/test_graph.py` `_initial_state` | 删 `new_message`/`raw_content`/`session_id`；加 `clean_text` 默认并更新各用例的 `raw_content=` → `clean_text=`（值改为已清洗文本） |
| `tests/test_handler_media.py` | `raw_content=` → `clean_text=`（值改为已清洗文本） |

无需改动的测试：`test_call_llm_node.py`（经 `make_state` 继承新默认）、`test_describe_image.py`、`test_routing.py`、`test_content_parser.py`。

## §4 行为不变保证

- `llm_text` 每轮必注入（`to_llm_text` 返回 `str`，永不 `None`）→ `_strip_mention` 兜底本就不可达，删除无行为变化
- `clean_text` 与 `parse_content` 内同一函数 → RAG 索引输出逐字节一致
- `session_id` 只进日志 → 改打 `thread_id` 无功能损失（`thread_id` 已含 guild:channel）

## §5 测试与验证

- 既有断言全部不变，仅改 state 构造方式
- 验收：`uv run pytest` 全绿
- 同步更新 `CLAUDE.md`：架构注释中的 `session_id` 定义、RAG 段 `clean_text(raw_content)` 描述、@-mention Gotcha 中 `_strip_mention` 兜底描述、BotState 字段清单

## §6 Pydantic / 嵌套 State 的分析结论（已决策，附记录）

- `pydantic>=2.0.0` 已在依赖（2.13.4）；langgraph 1.2.2 + langchain-core 1.4.9 原生支持 Pydantic State。
- 收益仅剩"默认值消除 `state.get` 防御代码 + 属性访问 + 内联文档"，但：
  - **无外部校验需求**：BotState 全部字段由 handler/图节点写入，唯一外部输入 `raw_content` 已在进图前经 `content_parser → ParsedContent` 类型化；
  - **嵌套不改变 LangGraph 行为**：state 所有字段跨轮持久化，与是否嵌套无关（每轮瞬态 ingress 字段照常被 handler 覆盖、体量小）；
  - 迁移成本：6 节点 + 4 测试文件全改访问方式，嵌套更冗长，每 super-step 默认多一次 schema 校验。
- **结论：维持 TypedDict + flat 字段，不引入 Pydantic、不嵌套。** "分类/打包"诉求已在进图前由 `ParsedContent` 满足，图内 flat 字段是合理的"传输类型 / 图 schema"分工。
