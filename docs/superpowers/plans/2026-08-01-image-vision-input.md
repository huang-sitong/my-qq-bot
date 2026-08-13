# 图片视觉输入（Ollama qwen3-vl:2b）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用本地 Ollama 视觉模型 `qwen3-vl:2b` 为图片消息生成中文描述，注入主 LLM 组织回复，并把描述写入 RAG 索引。

**Architecture:** 图内新增 `describe_image` 节点，夹在 `detect_intent`（回复路径）与 `call_llm` 之间。节点经 `VisionService` 下载图片（公网 URL → base64）并调 Ollama `POST /api/generate` 得描述，把 HumanMessage 里的 `[图片]` 原位替换成 `[图片：描述]`，描述另写 `vision_desc` 供 `index_turn` 索引用。任何视觉失败降级为保留 `[图片]` 占位符，不阻塞回复。

**Tech Stack:** Python 3.12、uv、LangGraph（StateGraph）、httpx（下载 + Ollama API）、pytest。无新依赖。

## Global Constraints

- Python >= 3.12，`uv run pytest tests/ -q` 跑测试。
- 遵循现有 nodes 模式：节点用 `functools.partial` 注入依赖，独立 `async def(state, ...) -> dict`。
- 视觉失败一律**降级不抛出**：图片仍以 `[图片]` 占位符进入 LLM，回复正常进行。
- `vision_desc` 只在 `content_kind=="image"` 轮生效（防 checkpoint 跨轮污染）。
- 不引入新依赖（图片下载与 Ollama 调用都用现有 `httpx`）。
- **提交：仅经用户明确确认后执行**（本仓库惯例），计划中 commit 步骤执行前需先征得同意。

---
---

### Task 1: VisionService（图片下载 + Ollama 视觉推理）

**Files:**
- Create: `bot/core/vision/service.py`
- Test: `tests/test_vision_service.py`

**Interfaces:**
- Consumes: 无（新模块）。`httpx.AsyncClient`。
- Produces: `VisionService(base_url, model, prompt=VISION_PROMPT, timeout=60.0, max_images=3, http=None)`；`async describe(src: str) -> str`（失败返回 ""）；`async describe_many(srcs: list[str]) -> list[str]`；`async close()`。

- [ ] **Step 1: 写失败测试** `tests/test_vision_service.py`

```python
"""VisionService：图片下载 → base64 → Ollama /api/generate 生成描述。"""

import asyncio
import base64

import httpx

from bot.core.vision.service import VISION_PROMPT, VisionService

IMG = "https://multimedia.nt.qq.com.cn/download?appid=1&fileid=abc"
GEN = "http://localhost:11434/api/generate"


class FakeResponse:
    def __init__(self, status=200, content=b"", json_data=None):
        self.status_code = status
        self.content = content
        self._json = json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPError(f"status {self.status_code}")

    def json(self):
        return self._json


class FakeClient:
    """按 URL 返回预设响应的假 httpx.AsyncClient，记录请求。"""

    def __init__(self, responses):
        self.responses = responses
        self.requests = []

    async def get(self, url, **kwargs):
        self.requests.append(("get", url))
        if url not in self.responses:
            raise httpx.HTTPError(f"no response for {url}")
        return self.responses[url]

    async def post(self, url, json=None, **kwargs):
        self.requests.append(("post", url, json))
        if url not in self.responses:
            raise httpx.HTTPError(f"no response for {url}")
        return self.responses[url]


def _svc(client, max_images=3):
    return VisionService(base_url="http://localhost:11434", model="qwen3-vl:2b",
                         http=client, max_images=max_images)


def test_describe_downloads_and_generates():
    png = b"\x89PNG\r\n\x1a\n"
    client = FakeClient({
        IMG: FakeResponse(content=png),
        GEN: FakeResponse(json_data={"response": "一只猫坐在窗台上"}),
    })
    svc = _svc(client)
    assert asyncio.run(svc.describe(IMG)) == "一只猫坐在窗台上"
    post = [r for r in client.requests if r[0] == "post"]
    assert len(post) == 1
    payload = post[0][2]
    assert payload["model"] == "qwen3-vl:2b"
    assert payload["stream"] is False
    assert payload["prompt"] == VISION_PROMPT
    assert payload["images"] == [base64.b64encode(png).decode("ascii")]


def test_describe_download_failure_returns_empty():
    svc = _svc(FakeClient({IMG: FakeResponse(status=403, content=b"")}))
    assert asyncio.run(svc.describe(IMG)) == ""


def test_describe_ollama_failure_returns_empty():
    svc = _svc(FakeClient({
        IMG: FakeResponse(content=b"data"),
        GEN: FakeResponse(status=500),
    }))
    assert asyncio.run(svc.describe(IMG)) == ""


def test_describe_missing_src_returns_empty():
    svc = _svc(FakeClient({}))
    assert asyncio.run(svc.describe(IMG)) == ""


def test_describe_many_caps_at_max_images():
    client = FakeClient({
        f"{IMG}1": FakeResponse(content=b"a"),
        f"{IMG}2": FakeResponse(content=b"b"),
        f"{IMG}3": FakeResponse(content=b"c"),
        GEN: FakeResponse(json_data={"response": "图"}),
    })
    svc = _svc(client, max_images=2)
    assert asyncio.run(svc.describe_many([f"{IMG}1", f"{IMG}2", f"{IMG}3"])) == ["图", "图"]


def test_describe_many_partial_failure():
    client = FakeClient({
        f"{IMG}1": FakeResponse(content=b"a"),
        GEN: FakeResponse(json_data={"response": "图"}),
    })
    svc = _svc(client)
    assert asyncio.run(svc.describe_many([f"{IMG}1", f"{IMG}2"])) == ["图", ""]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_vision_service.py -q`
Expected: `ModuleNotFoundError: No module named 'bot.core.vision'`

- [ ] **Step 3: 实现** `bot/core/vision/service.py`

```python
"""VisionService — 图片下载 + Ollama 视觉推理（qwen3-vl）。

图片以公网 URL（如腾讯多媒体 CDN）到达，Ollama 的 images 参数只收 base64，
因此先下载 → base64 → POST {base_url}/api/generate 生成描述。
失败不抛出：describe 返回 ""（调用方降级为 [图片] 占位符）。
"""

import base64
import logging

import httpx

logger = logging.getLogger(__name__)

# 轻量、中文友好的图片描述提示词
VISION_PROMPT = "请用中文简要描述这张图片的内容。"


class VisionService:
    """下载图片并调用本地 Ollama 视觉模型生成中文描述。"""

    def __init__(
        self,
        base_url: str,
        model: str,
        prompt: str = VISION_PROMPT,
        timeout: float = 60.0,
        max_images: int = 3,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.prompt = prompt
        self.timeout = timeout
        self.max_images = max_images
        self._owns_http = http is None
        self._http = http or httpx.AsyncClient(timeout=timeout)

    async def describe(self, src: str) -> str:
        """下载一张图并返回描述；失败返回空串（不抛出）。"""
        try:
            image_b64 = await self._download_base64(src)
            if not image_b64:
                return ""
            return await self._ollama_generate(image_b64)
        except Exception:
            logger.warning("Vision describe failed for %s", src, exc_info=True)
            return ""

    async def describe_many(self, srcs: list[str]) -> list[str]:
        """逐个描述，最多 max_images 张；单张失败返回 ""。"""
        descs = []
        for src in srcs[: self.max_images]:
            descs.append(await self.describe(src))
        return descs

    async def _download_base64(self, src: str) -> str:
        resp = await self._http.get(src)
        resp.raise_for_status()
        return base64.b64encode(resp.content).decode("ascii")

    async def _ollama_generate(self, image_b64: str) -> str:
        payload = {
            "model": self.model,
            "prompt": self.prompt,
            "images": [image_b64],
            "stream": False,
        }
        resp = await self._http.post(f"{self.base_url}/api/generate", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return (data.get("response") or "").strip()

    async def close(self) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()
            self._http = None
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_vision_service.py -q`
Expected: 6 passed

- [ ] **Step 5: Commit（需用户确认）**

```bash
git add bot/core/vision/service.py tests/test_vision_service.py
git commit -m "feat: VisionService 图片下载 + Ollama 视觉推理"
```

---
---

### Task 2: describe_image_node（视觉节点）

**Files:**
- Create: `bot/core/nodes/action_node/describe_image.py`
- Test: `tests/test_describe_image.py`
- Modify: `tests/fakes.py`（加 `FakeVisionService`）

**Interfaces:**
- Consumes: `VisionService.describe_many(srcs) -> list[str]`（Task 1）；`make_state`（fakes）；BotState 的 `image_srcs` / `messages`。
- Produces: `describe_image_node(state, vision_service) -> dict`；模块函数 `replace_placeholders(content, descriptions) -> str`（Task 4 图接线依赖本节点）。

- [ ] **Step 1: 在 fakes 加 `FakeVisionService`** `tests/fakes.py`

在 `StubMemoryStore` 后追加：

```python
class FakeVisionService:
    """脚本化描述序列的假视觉服务。"""

    def __init__(self, descriptions=None):
        self.descriptions = descriptions or []
        self.calls = 0
        self.last_srcs = None

    async def describe_many(self, srcs):
        self.calls += 1
        self.last_srcs = srcs
        return list(self.descriptions)
```

- [ ] **Step 2: 写失败测试** `tests/test_describe_image.py`

```python
"""describe_image_node：视觉描述注入 HumanMessage（原位替换、降级保留占位符）。"""

import asyncio

from langchain_core.messages import HumanMessage

from bot.core.nodes.action_node.describe_image import describe_image_node
from tests.fakes import FakeVisionService, make_state


def _state(msg, **overrides):
    return make_state(
        messages=[msg],
        content_kind="image",
        image_srcs=["u1", "u2"],
        **overrides,
    )


def test_noop_when_vision_disabled():
    msg = HumanMessage(content="[图片]")
    assert asyncio.run(describe_image_node(_state(msg), None)) == {}


def test_noop_without_image_srcs():
    fake = FakeVisionService()
    state = make_state(messages=[HumanMessage(content="你好")], content_kind="text")
    assert asyncio.run(describe_image_node(state, fake)) == {}
    assert fake.calls == 0


def test_replaces_placeholders_in_order():
    fake = FakeVisionService(["猫", "狗"])
    msg = HumanMessage(content="看 [图片] 和 [图片]")
    result = asyncio.run(describe_image_node(_state(msg), fake))
    new_msg = result["messages"][0]
    assert isinstance(new_msg, HumanMessage)
    assert new_msg.content == "看 [图片：猫] 和 [图片：狗]"
    assert new_msg.id == msg.id  # 原位替换，不产生重复消息
    assert result["vision_desc"] == "猫；狗"


def test_partial_failure_keeps_placeholder():
    fake = FakeVisionService(["", "狗"])
    msg = HumanMessage(content="看 [图片] 和 [图片]")
    result = asyncio.run(describe_image_node(_state(msg), fake))
    assert result["messages"][0].content == "看 [图片] 和 [图片：狗]"
    assert result["vision_desc"] == "狗"


def test_all_failed_is_noop():
    fake = FakeVisionService(["", ""])
    msg = HumanMessage(content="[图片]")
    assert asyncio.run(describe_image_node(_state(msg), fake)) == {}
```

- [ ] **Step 3: 运行测试确认失败**

Run: `uv run pytest tests/test_describe_image.py -q`
Expected: `ModuleNotFoundError: No module named 'bot.core.nodes.action_node.describe_image'`

- [ ] **Step 4: 实现** `bot/core/nodes/action_node/describe_image.py`

```python
"""describe_image — 图片回复路径的视觉理解节点。

对图片消息调用 Ollama 视觉模型生成描述，把 HumanMessage 里的 [图片] 占位符
原位替换为 [图片：描述]，并把描述写入 vision_desc 供 RAG 索引。
视觉服务为 None 或非图片消息时 no-op（占位符保留，行为同旧版）。
"""

import logging

from langchain_core.messages import HumanMessage

from bot.core.vision.service import VisionService
from object.bot.state import BotState

logger = logging.getLogger(__name__)


def replace_placeholders(content: str, descriptions: list[str]) -> str:
    """把 content 里每个 [图片] 按序替换成 [图片：描述]；描述为空则保留 [图片]。"""
    marker = "[图片]"
    parts = []
    idx = 0
    for desc in descriptions:
        pos = content.find(marker, idx)
        if pos == -1:
            break
        parts.append(content[idx:pos])
        parts.append(f"[图片：{desc}]" if desc else marker)
        idx = pos + len(marker)
    parts.append(content[idx:])
    return "".join(parts)


async def describe_image_node(state: BotState, vision_service: VisionService | None) -> dict:
    """为图片消息生成描述并注入消息内容。失败时降级为 [图片] 占位符。"""
    if vision_service is None:
        return {}
    image_srcs = state.get("image_srcs") or []
    if not image_srcs:
        return {}
    messages = state.get("messages") or []
    if not messages or not isinstance(messages[-1], HumanMessage):
        return {}
    msg = messages[-1]
    descriptions = await vision_service.describe_many(image_srcs)
    new_content = replace_placeholders(msg.content, descriptions)
    vision_desc = "；".join(d for d in descriptions if d)
    if new_content == msg.content and not vision_desc:
        return {}  # 全部失败 → 无需改动
    return {
        "messages": [HumanMessage(content=new_content, id=msg.id)],  # 同 id → 原位替换
        "vision_desc": vision_desc,
    }
```

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/test_describe_image.py -q`
Expected: 5 passed

- [ ] **Step 6: Commit（需用户确认）**

```bash
git add bot/core/nodes/action_node/describe_image.py tests/test_describe_image.py tests/fakes.py
git commit -m "feat: describe_image 节点注入视觉描述"
```

---
---

### Task 3: BotState + BotConfig 接线

**Files:**
- Modify: `object/bot/state.py`
- Modify: `common/config.py`

**Interfaces:**
- Consumes: 无。
- Produces: BotState 新增 `image_srcs: list[str]`、`vision_desc: str`；BotConfig 新增 `vision_enabled` / `vision_model` / `vision_max_images` / `vision_timeout`（Task 4 的 handler 提取、Task 6 的 main 构建依赖）。

- [ ] **Step 1: state.py 加字段** `object/bot/state.py`

在 `llm_text` 字段后追加：

```python
    image_srcs: list[str]   # 本轮图片 URL（describe_image 视觉理解用）
    vision_desc: str        # 本轮图片描述（RAG 索引；仅 image 轮有效）
```

- [ ] **Step 2: config.py 加视觉配置** `common/config.py`

在 `rag_max_agent_rounds` 字段后（dataclass 末尾）追加：

```python
    # --- Vision (本地 Ollama 视觉模型，图片描述) ---
    vision_enabled: bool = field(
        default_factory=lambda: os.getenv("BOT_VISION_ENABLED", "1") not in ("0", "false", "False", ""),
    )
    vision_model: str = field(
        default_factory=lambda: os.getenv("BOT_VISION_MODEL", "qwen3-vl:2b"),
    )
    vision_max_images: int = field(
        default_factory=lambda: int(os.getenv("BOT_VISION_MAX_IMAGES", "3")),
    )
    vision_timeout: int = field(
        default_factory=lambda: int(os.getenv("BOT_VISION_TIMEOUT", "60")),
    )
```

- [ ] **Step 3: 验证接线无破坏**

Run: `uv run pytest tests/ -q`
Expected: 全量通过（76 passed, 1 skipped）。新增字段为纯声明，不影响现有测试。

- [ ] **Step 4: Commit（需用户确认）**

```bash
git add object/bot/state.py common/config.py
git commit -m "feat: BotState 增加 image_srcs/vision_desc，BotConfig 增加视觉配置"
```

---
---

### Task 4: 图 + handler 接线 + 图级测试

**Files:**
- Modify: `bot/core/graph.py`
- Modify: `bot/handler.py`
- Modify: `bot/core/nodes/action_node/__init__.py`
- Modify: `bot/core/nodes/__init__.py`
- Test: `tests/test_graph.py`

**Interfaces:**
- Consumes: `describe_image_node`（Task 2）、BotState `image_srcs`（Task 3）、`FakeVisionService`（fakes）。
- Produces: `create_graph(llm, config, db_dir="db", rag_service=None, memory_store=None, vision_service=None)`；图路由 `should_respond → "describe_image"`。

- [ ] **Step 1: action_node/__init__.py 导出**

`bot/core/nodes/action_node/__init__.py` 改为：

```python
from .describe_image import describe_image_node
from .detect_intent import detect_intent
from .index_turn import index_turn_node
from .summarize import summarize_node

__all__ = ["describe_image_node", "detect_intent", "index_turn_node", "summarize_node"]
```

`bot/core/nodes/__init__.py` 改为：

```python
from .action_node import describe_image_node, detect_intent, index_turn_node, summarize_node
from .llm_node import call_llm_node, router_node
from .tool_node import tool_node

__all__ = [
    "call_llm_node", "describe_image_node", "detect_intent", "index_turn_node",
    "router_node", "summarize_node", "tool_node",
]
```

- [ ] **Step 2: 写失败测试（图级）** `tests/test_graph.py`

在文件顶部 fakes 导入改为 `from tests.fakes import FakeVisionService, ScriptedLLM, StubMemoryStore, StubRagService`，文件末尾追加：

```python
def test_graph_image_reply_includes_vision_description(tmp_path):
    rag = StubRagService()
    vision = FakeVisionService(["一只猫坐在窗台上"])
    llm = ScriptedLLM([AIMessage(content="好可爱的猫！")])
    graph, _ = asyncio.run(
        create_graph(
            llm, BotConfig(rag_enabled=True), db_dir=str(tmp_path),
            rag_service=rag, vision_service=vision,
        )
    )
    state = {
        **_initial_state(),
        "content_kind": "image",
        "raw_content": '<img src="https://x/1.jpg"/>',
        "llm_text": "[图片]",
        "image_srcs": ["https://x/1.jpg"],
    }
    result = asyncio.run(graph.ainvoke(state, {"configurable": {"thread_id": "test:thread"}}))

    assert result["reply_text"] == "好可爱的猫！"
    humans = [m for m in result["messages"] if isinstance(m, HumanMessage)]
    assert humans and humans[0].content == "[图片：一只猫坐在窗台上]"
    assert rag.last_indexed is not None
    assert "一只猫坐在窗台上" in rag.last_indexed["user_message"]


def test_graph_image_reply_without_vision_keeps_placeholder(tmp_path):
    rag = StubRagService()
    llm = ScriptedLLM([AIMessage(content="我看不到图")])
    graph, _ = asyncio.run(
        create_graph(llm, BotConfig(rag_enabled=True), db_dir=str(tmp_path), rag_service=rag)
    )
    state = {
        **_initial_state(),
        "content_kind": "image",
        "raw_content": '<img src="https://x/1.jpg"/>',
        "llm_text": "[图片]",
        "image_srcs": ["https://x/1.jpg"],
    }
    result = asyncio.run(graph.ainvoke(state, {"configurable": {"thread_id": "test:thread"}}))

    assert result["reply_text"] == "我看不到图"
    humans = [m for m in result["messages"] if isinstance(m, HumanMessage)]
    assert humans and humans[0].content == "[图片]"  # 占位符保留
    assert rag.last_indexed is None  # 纯图片无描述 → 不入库
```

- [ ] **Step 3: 运行测试确认失败**

Run: `uv run pytest tests/test_graph.py::test_graph_image_reply_includes_vision_description -q`
Expected: FAIL（`TypeError: create_graph() got an unexpected keyword argument 'vision_service'`）

- [ ] **Step 4: graph.py 接线** `bot/core/graph.py`

- import 列表加 `describe_image_node`：
```python
from bot.core.nodes import (
    call_llm_node,
    describe_image_node,
    detect_intent,
    index_turn_node,
    summarize_node,
    tool_node,
)
```
- `_route_after_detect` 改为 `should_respond` 返回 `"describe_image"`：
```python
def _route_after_detect(state: BotState) -> str:
    """Deterministic 3-way route from detect_intent (no LLM router).

    - should_respond → describe_image (vision for image turns, no-op for text) → call_llm
    - non-replied text → summarize (context + compression + single-record index)
    - non-replied media (image group non-@ / file / audio / video) → END
    """
    if state.get("should_respond", False):
        return "describe_image"
    if state.get("content_kind", "") == "text":
        return "summarize"
    return END
```
- `create_graph` 签名加 `vision_service=None`，并注册节点、加边：
```python
async def create_graph(
    llm: ChatOpenAI,
    config: BotConfig,
    db_dir: str = "db",
    rag_service=None,
    memory_store=None,
    vision_service=None,
) -> tuple[CompiledStateGraph, AsyncSqliteSaver]:
```
```python
    builder.add_node("describe_image", partial(describe_image_node, vision_service=vision_service))
```
```python
    builder.add_edge("describe_image", "call_llm")
```

- [ ] **Step 5: handler.py 提取 image_srcs** `bot/handler.py`

`_process` 中 `parsed = parse_content(raw_content)` 后加一行，并在初始 state dict 的 `"llm_text": parsed.llm_text,` 后加 `"image_srcs": image_srcs,`：

```python
        parsed = parse_content(raw_content)
        content_kind = parsed.kind.value
        image_srcs = [a.src for a in parsed.attachments if a.type == "img"]
```

- [ ] **Step 6: 运行测试确认通过**

Run: `uv run pytest tests/test_graph.py tests/test_handler_media.py tests/test_detect_intent.py -q`
Expected: 全部通过（含新图级测试）

- [ ] **Step 7: Commit（需用户确认）**

```bash
git add bot/core/graph.py bot/handler.py bot/core/nodes/action_node/__init__.py bot/core/nodes/__init__.py tests/test_graph.py
git commit -m "feat: 图接入 describe_image 节点，handler 注入 image_srcs"
```

---
---

### Task 5: index_turn 视觉索引集成

**Files:**
- Modify: `bot/core/nodes/action_node/index_turn.py`
- Test: `tests/test_handler_media.py`

**Interfaces:**
- Consumes: BotState `content_kind` / `vision_desc`（Task 3）、`clean_text`、`RagService.index_turn`。
- Produces: `index_turn_node` 在 `content_kind=="image"` 且有 `vision_desc` 时把描述并入索引内容。

- [ ] **Step 1: 更新纯媒体测试语义** `tests/test_handler_media.py`

`test_index_turn_skips_media_only` 补 `content_kind="image"`（更贴近真实；无描述仍跳过）：

```python
def test_index_turn_skips_media_only():
    rag = StubRagService()
    _run(rag, raw_content='<img src="x"/>', reply_text="收到", content_kind="image")
    assert rag.last_indexed is None
```

- [ ] **Step 2: 写失败测试** `tests/test_handler_media.py` 末尾追加

```python
def test_index_turn_appends_vision_desc_for_image():
    rag = StubRagService()
    _run(rag, raw_content='<img src="https://x/1.jpg"/>', reply_text="收到",
         content_kind="image", vision_desc="一只猫")
    assert rag.last_indexed is not None
    assert rag.last_indexed["user_message"] == "[图片：一只猫]"


def test_index_turn_image_without_vision_skips():
    rag = StubRagService()
    _run(rag, raw_content='<img src="https://x/1.jpg"/>', reply_text="收到",
         content_kind="image")
    assert rag.last_indexed is None


def test_index_turn_text_ignores_stale_vision_desc():
    rag = StubRagService()
    # text 轮残留上一张图的 vision_desc → content_kind=="text" 过滤，不追加
    _run(rag, raw_content="晚上吃什么", reply_text="去吃火锅",
         content_kind="text", vision_desc="一只猫")
    assert rag.last_indexed is not None
    assert rag.last_indexed["user_message"] == "晚上吃什么"
```

- [ ] **Step 3: 运行测试确认失败**

Run: `uv run pytest tests/test_handler_media.py -q`
Expected: `test_index_turn_appends_vision_desc_for_image` FAIL（`last_indexed` 为 None，因 content 为空且未追加描述）

- [ ] **Step 4: 实现** `bot/core/nodes/action_node/index_turn.py`

`index_turn_node` 函数体改为：

```python
async def index_turn_node(state: BotState, rag_service: RagService | None) -> dict:
    """Index the current turn into the vector store. No-op when RAG is disabled."""
    if rag_service is None:
        return {}
    content = clean_text(state.get("raw_content", ""))
    vision_desc = state.get("vision_desc", "").strip()
    # vision_desc 经 checkpoint 跨轮持久，仅 image 轮才并入索引内容
    if state.get("content_kind") == "image" and vision_desc:
        content = f"{content} [图片：{vision_desc}]".strip()
    if not content.strip():
        return {}  # 纯媒体且无描述 — nothing meaningful to index
    await rag_service.index_turn(
        thread_id=state.get("thread_id", ""),
        user_id=state.get("user_id", ""),
        user_name=state.get("user_name", ""),
        user_message=content,
        bot_reply=state.get("reply_text", ""),  # empty → service indexes user only
    )
    return {}
```

同时更新模块 docstring 的"回复轮索引 2 条"说明，补充"图片轮带视觉描述则并入用户消息"。

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/test_handler_media.py -q`
Expected: 全部通过（含 3 个新测试 + 更新的 media-only）

- [ ] **Step 6: Commit（需用户确认）**

```bash
git add bot/core/nodes/action_node/index_turn.py tests/test_handler_media.py
git commit -m "feat: index_turn 图片轮并入视觉描述"
```

---
---

### Task 6: main.py + bot/__init__.py 接线

**Files:**
- Modify: `bot/__init__.py`
- Modify: `main.py`

**Interfaces:**
- Consumes: `VisionService`（Task 1）、`config.vision_*` / `config.ollama_base_url`（Task 3）、`create_graph(..., vision_service=...)`（Task 4）。
- Produces: 应用启动时按 `vision_enabled` 构建 `VisionService` 并注入图；shutdown 关闭。

- [ ] **Step 1: bot/__init__.py 导出 VisionService**

`bot/__init__.py` 加：

```python
from .core.vision.service import VisionService
```

并把 `"VisionService"` 加进 `__all__`。

- [ ] **Step 2: main.py 接线**

- import 加 `VisionService`。
- `create_graph` 调用前构建并传入：

```python
    vision_service = None
    if config.vision_enabled:
        vision_service = VisionService(
            base_url=config.ollama_base_url,
            model=config.vision_model,
            timeout=config.vision_timeout,
            max_images=config.vision_max_images,
        )
    graph, checkpointer = await create_graph(
        llm, config, db_dir=config.db_dir, rag_service=rag_service, memory_store=memory_store,
        vision_service=vision_service,
    )
```

- shutdown 的 `finally` 块中、`memory_store.close()` 前加：

```python
        if vision_service is not None:
            await vision_service.close()
```

- [ ] **Step 3: 验证**

Run: `uv run python -c "import main; import bot; from bot import VisionService; print('ok')"`
Expected: `ok`（无导入错误）

Run: `uv run pytest tests/ -q`
Expected: 全量通过

- [ ] **Step 4: Commit（需用户确认）**

```bash
git add bot/__init__.py main.py
git commit -m "feat: main 构建 VisionService 并注入图，shutdown 关闭"
```

---
---

### Task 7: CLAUDE.md 同步 + 全量回归

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: CLAUDE.md 更新**

- 架构树 `core/` 下加：
```
      vision/                  #   Ollama 视觉模型（图片描述）
        service.py             #     VisionService — 下载图片 → base64 → /api/generate
```
- 数据流 `detect_intent` 行后插入：
```
    → describe_image (action_node)  ← 图片回复路径：下载→Ollama qwen3-vl 描述→[图片] 原位替换；非图片/禁用 no-op
```
- 配置行（RAG 后）追加视觉 env：`BOT_VISION_ENABLED` / `BOT_VISION_MODEL` / `BOT_VISION_MAX_IMAGES` / `BOT_VISION_TIMEOUT`。
- 索引说明：图片回复轮描述并入用户消息索引；纯图片无描述不入库。

- [ ] **Step 2: 全量回归**

Run: `uv run pytest tests/ -q`
Expected: 全量通过（76 既有 + 新增，约 90+ passed, 1 skipped）

- [ ] **Step 3: Commit（需用户确认）**

```bash
git add CLAUDE.md
git commit -m "docs: CLAUDE.md 同步视觉节点与配置"
```

---
---

## 自审记录

- 规范覆盖：VisionService（T1）、describe_image_node（T2）、state/config（T3）、图+handler 接线（T4）、索引集成（T5）、main 接线（T6）、CLAUDE.md（T7）—— 规范各节均有对应任务。
- 类型一致：`describe_many`、`create_graph(..., vision_service=...)`、`image_srcs`/`vision_desc`、`config.vision_*` 在各任务间签名一致。
- 无占位符：所有步骤含完整代码与预期输出。
- 已知边界：`describe_image` 全失败返回 `{}`（占位符保留）；`vision_desc` 跨轮污染由 `content_kind=="image"` 过滤兜底。
