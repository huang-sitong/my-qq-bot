# 图片视觉输入设计（本地 Ollama qwen3-vl:2b）

日期：2026-08-01

## Context

现状：图片消息只以 `[图片]` 占位符进入 LLM，主 LLM（sensenova 文本模型）看不到图片内容。

目标：用本地 Ollama 轻量视觉模型 `qwen3-vl:2b` 生成图片描述，把描述注入主 LLM 组织回复，并把描述写入 RAG 索引，让以后能按图片内容检索。

关键事实：

- 真实图片 src 是公网 HTTP URL（`https://multimedia.nt.qq.com.cn/download?appid=...&fileid=...`），`parse_attachments` 已能解析并 unescape 出 `Attachment.src`。
- Ollama 的 `images` 参数只收 base64，不能直接传 URL → 需先下载图片再 base64。
- 主 LLM 经 `ChatOpenAI`（OpenAI 兼容端点）接入，文本模型，不接收图片。
- 附件 src 目前不进入图状态（handler 算了 `parsed.attachments` 但没存进 BotState）。

范围（已定）：视觉只作用于**回复路径**（私聊图片、群聊@图片）。群聊非@图片仍走 END（不入上下文、不索引，此前已定）。

## 方案（已批准：方案 A）

图内新增 `describe_image` 节点，夹在 `detect_intent` 与 `call_llm` 之间，只在回复路径执行；文本消息直接 no-op。

## 组件

### 1. `bot/core/vision/service.py`（新增）— `VisionService`

图片下载 + Ollama 视觉推理的组合服务：

```python
class VisionService:
    def __init__(self, base_url, model, prompt, timeout, max_images, http=None):
        ...
    async def describe(self, src: str) -> str:
        # httpx GET src → bytes → base64（不带 data: 前缀）→ Ollama → 描述文本
    async def describe_many(self, srcs: list[str]) -> list[str]:
        # 逐个 describe，失败返回 ""；最多处理 max_images 张
    def close(self) -> None: ...
```

- 下载：`httpx.AsyncClient` GET 图片 URL → bytes → base64。
- 推理：Ollama 原生 `POST {base_url}/api/generate`，payload `{"model", "prompt", "images":[b64], "stream":false}`，取 `response["response"]`。不走 langchain（避免 ChatOllama 视觉的坑，更可控）。
- 可注入 `http` client 便于测试。
- 视觉提示词用模块常量 `VISION_PROMPT`（"请用中文简要描述这张图片的内容。"）。

### 2. `bot/core/nodes/action_node/describe_image.py`（新增）— `describe_image_node`

```python
async def describe_image_node(state, vision_service) -> dict:
    if vision_service is None or not state.get("image_srcs"):
        return {}  # 禁用/非图片消息 → no-op
    msg = state["messages"][-1]                      # detect_intent 建的 HumanMessage
    descs = await vision_service.describe_many(state["image_srcs"])
    new_content = _replace_placeholders(msg.content, descs)   # [图片] → [图片：desc]
    return {
        "messages": [HumanMessage(content=new_content, id=msg.id)],  # 同 id → 原位替换
        "vision_desc": "；".join(d for d in descs if d),             # 供 RAG 索引
    }
```

关键：返回的 HumanMessage **沿用原 id**，`add_messages` 按 id 原位替换，不产生重复消息。

### 3. `bot/core/graph.py`

- `builder.add_node("describe_image", partial(describe_image_node, vision_service=vision_service))`
- `_route_after_detect`：`should_respond` → `"describe_image"`（不再直连 call_llm）
- `describe_image → call_llm`

### 4. `object/bot/state.py` 新增字段

- `image_srcs: list[str]` — 本轮图片 URL（handler 从 `parsed.attachments` 提取 `type=="img"`）
- `vision_desc: str` — 本轮图片描述（index_turn 索引用）

### 5. `bot/handler.py` `_process`

- `image_srcs = [a.src for a in parsed.attachments if a.type == "img"]`，注入初始 state。

### 6. `common/config.py` 新增

- `vision_enabled`（`BOT_VISION_ENABLED`，默认 on）
- `vision_model`（`BOT_VISION_MODEL`，默认 `qwen3-vl:2b`）
- `vision_max_images`（`BOT_VISION_MAX_IMAGES`，默认 3）
- `vision_timeout`（`BOT_VISION_TIMEOUT`，默认 60）
- 复用 `ollama_base_url`（`OLLAMA_BASE_URL`，默认 `http://localhost:11434`）

### 7. `main.py`

- `vision_service = VisionService(...) if config.vision_enabled else None` → 传入 create_graph
- shutdown 时 `vision_service.close()`

### 8. `bot/core/nodes/action_node/index_turn.py`（索引集成）

```python
content = clean_text(state.get("raw_content", ""))
vision_desc = state.get("vision_desc", "").strip()
if state.get("content_kind") == "image" and vision_desc:
    content = f"{content} [图片：{vision_desc}]".strip()
if not content.strip():
    return {}  # 纯媒体且无描述 → 跳过
```

- **仅当本轮 `content_kind=="image"` 才追加描述** —— `vision_desc` 经 checkpoint 跨轮持久，若不按 content_kind 过滤，下一轮纯文本消息会误带上一张图的描述入库。
- 附带收益：**纯图片回复轮现在也会入库**（描述让 `content` 非空），此前纯图片被跳过。

## 数据流

```
图片消息（私聊 / 群聊@）
  → handler: content_kind="image", llm_text="看看这张 [图片]", image_srcs=[腾讯URL]
  → detect_intent: should_respond=True, messages=[HumanMessage("看看这张 [图片]")]
  → describe_image: 下载→base64→Ollama qwen3-vl:2b → "一只猫坐在窗台上"
        messages=[HumanMessage("看看这张 [图片：一只猫坐在窗台上]", id=同id)]
        vision_desc="一只猫坐在窗台上"
  → call_llm: 主 LLM 用描述组织回复
  → summarize → index_turn → END（索引内容含 " [图片：一只猫坐在窗台上]"）
```

## 错误处理

| 场景 | 行为 |
|---|---|
| `vision_service is None`（禁用/未配置） | describe_image no-op，`[图片]` 占位符保留，行为同现状 |
| 图片下载失败（URL 403/过期） | 记 warning，该图返回 ""，保留 `[图片]`，**不阻塞回复** |
| Ollama 失败（模型未拉取/超时） | 同上，降级 |
| 部分图片失败 | 成功的用描述，失败的留 `[图片]` |
| 超时 | `vision_timeout`（默认 60s）包住下载 + 推理 |

延迟：每次图片回复增加一次下载 + Ollama 推理（秒级），同步发生在回复轮内。属预期开销。

## 配置默认值

| env | 默认 | 说明 |
|---|---|---|
| `BOT_VISION_ENABLED` | `1`（on） | 总开关 |
| `BOT_VISION_MODEL` | `qwen3-vl:2b` | Ollama 模型名（含 tag） |
| `BOT_VISION_MAX_IMAGES` | `3` | 单条消息最多理解的图片数 |
| `BOT_VISION_TIMEOUT` | `60` | 下载+推理超时（秒） |
| `OLLAMA_BASE_URL` | 复用现有 | 默认 `http://localhost:11434` |

## 测试

- **新增 `tests/test_vision_service.py`** — `VisionService` 单测（注入 fake httpx client，不碰真实网络/Ollama）：下载→base64→POST payload 正确（含 model/prompt/images/stream）；返回 `response["response"]`；下载失败/Ollama 失败返回 ""；`describe_many` 按序、上限 max_images、部分失败返回 ""。
- **新增 `tests/test_describe_image.py`** — `describe_image_node` 单测（`FakeVisionService` 注入）：`vision_service is None` no-op；无 `image_srcs` no-op；多个 `[图片]` 按序替换；返回 HumanMessage id 与原消息相同；失败占位符保留；设置 `vision_desc`。
- **`tests/fakes.py`** — 加 `FakeVisionService`（脚本化描述 + 计数）。
- **`tests/test_graph.py`** — 图级：群聊@图片 + fake vision → 最终 HumanMessage 带 `[图片：描述]`、`vision_desc` 入索引、回复正常；`vision_service=None` → `[图片]` 占位符保留。
- **`tests/test_handler_media.py`**（index_turn）：image 轮带 `vision_desc` → 索引进含描述；image 轮无描述 → 纯媒体跳过；text 轮 state 残留旧 `vision_desc` → 不追加。
- **`CLAUDE.md`** 同步：架构树 vision/、数据流 describe_image、配置、gotcha。

## 不改动

- 主 LLM 接入（sensenova 文本模型）不变。
- 群聊非@图片路由（END）不变。
- router 文件保留、图内不接线（此前决定）不变。
