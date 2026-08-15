# Vision OpenAI 兼容 Provider 改造 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `VisionService` 的图片描述调用从本地 Ollama `/api/generate` 改成 OpenAI 兼容的 `/v1/chat/completions`，新增 `BOT_VISION_API_KEY` 配置（未设回落主 LLM 的 `API_KEY`）。

**Architecture:** `VisionService` 对外接口（`describe` / `describe_many` / `download_images_as_data_urls`）不变，仅把内部生成逻辑从 Ollama 生成协议换成 OpenAI 兼容 chat completions；`BotConfig` 视觉段新增 `vision_api_key` 字段，`vision_base_url` / `vision_api_key` 未设时在 `model_validator` 里回落主 LLM（`BASE_URL` / `API_KEY`）。`describe_image_node`、`graph.py`、多模态主 LLM 路径均无感知。

**Tech Stack:** Python 3.12、pydantic-settings（BotConfig）、httpx（VisionService 裸 HTTP 客户端，已在依赖）、pytest。

**Spec:** 无独立 spec 文件（bounded 任务，设计经 chat 确认：直接替换 Ollama、独立字段回落主 LLM）。

## Global Constraints

- `docs/superpowers/` 与 `docs/plans/` 下的 spec/plan **从不提交**（本地工作文档）；实现代码按常规提交。
- 测试沿用仓库既有风格：`tests/test_config.py`（BotConfig 默认值/env 映射/回落）、`tests/test_vision_service.py`（FakeClient + FakeResponse 假 httpx）。
- `tests/test_config.py::test_env_template_contains_all_env_aliases` 强制：**新增 env alias 必须同步出现在 `.env-template`**，否则该测试失败。
- `tests/test_config.py::test_every_field_has_env_alias` 强制：**新增字段必须同步进 `EXPECTED_DEFAULTS`**。
- 不引入新依赖（httpx / pydantic-settings 已存在）。
- 行为降级不变：视觉失败返回 `""` → 调用方降级为 `[图片]` 占位符；失败绝不抛出。

---

### Task 1: BotConfig 新增 `vision_api_key` + 视觉回落逻辑改为主 LLM

**Files:**
- Modify: `src/common/config.py:234-247`（视觉段字段）、`src/common/config.py:348-356`（model_validator）
- Modify: `tests/test_config.py:13-71`（EXPECTED_DEFAULTS）、`74-132`（ENV_SAMPLES）、`250-273`（回落测试）
- Modify: `.env-template:51-55`（Vision 段注释，供 `test_env_template_contains_all_env_aliases`）

**Interfaces:**
- Produces: `BotConfig.vision_api_key: str | None`（alias `BOT_VISION_API_KEY`）、`BotConfig.vision_base_url` 改为 `str | None`，默认 `None`；未设 `vision_base_url` 且 `llm_base_url` 非空 → 回落 `llm_base_url`；未设 `vision_api_key` → 回落 `llm_api_key`。embed 段 Ollama 回落**不动**。

- [ ] **Step 1: 写失败测试（config）**

改 `tests/test_config.py`：

```python
EXPECTED_DEFAULTS = {
    ...
    "vision_base_url": None,        # 原 "http://localhost:11434"
    "vision_api_key": None,         # 新增
    ...
}
```

`ENV_SAMPLES` 增一行：

```python
    "vision_api_key": ("vkey", "vkey"),
```

把 `test_embed_and_vision_urls_fallback_to_ollama`（原 260-265 行）整体替换为两条回落测试，并更新 `test_embed_url_does_not_leak_to_vision`（原 268-273 行）：

```python
def test_embed_url_falls_back_to_ollama(monkeypatch):
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://legacy.local")
    config = BotConfig(_env_file=None)
    assert config.embed_base_url == "http://legacy.local"


def test_vision_url_and_key_fall_back_to_main_llm(monkeypatch):
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("BASE_URL", "https://llm.example.com/v1")
    monkeypatch.setenv("API_KEY", "sk-main")
    config = BotConfig(_env_file=None)
    assert config.vision_base_url == "https://llm.example.com/v1"
    assert config.vision_api_key == "sk-main"


def test_vision_explicit_url_and_key_win_over_fallback(monkeypatch):
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("BASE_URL", "https://llm.example.com/v1")
    monkeypatch.setenv("API_KEY", "sk-main")
    monkeypatch.setenv("BOT_VISION_BASE_URL", "https://vision.example.com/v1")
    monkeypatch.setenv("BOT_VISION_API_KEY", "sk-vision")
    config = BotConfig(_env_file=None)
    assert config.vision_base_url == "https://vision.example.com/v1"
    assert config.vision_api_key == "sk-vision"


def test_embed_url_does_not_leak_to_vision(monkeypatch):
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("BOT_EMBED_BASE_URL", "http://embed.local")
    config = BotConfig(_env_file=None)
    assert config.embed_base_url == "http://embed.local"
    assert config.vision_base_url is None
    assert config.vision_api_key is None
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /f/PythonProject/qq-bot && uv run pytest tests/test_config.py -q`
Expected: FAIL —— `vision_api_key` 字段不存在（AttributeError/KeyError）、`vision_base_url` 默认断言失败、回落逻辑仍是 Ollama。

- [ ] **Step 3: 实现配置改动**

`src/common/config.py` 视觉段（`vision_base_url` 字段 181-184 行区域附近，`vision_api_key` 加在 `vision_model` 之后）：

```python
    vision_base_url: str | None = Field(
        default=None,
        validation_alias="BOT_VISION_BASE_URL",
    )
    ...
    # OpenAI 兼容视觉 API key；未设时回落主 LLM 的 API_KEY（同供应商零配置）
    vision_api_key: str | None = Field(
        default=None,
        validation_alias="BOT_VISION_API_KEY",
    )
```

`model_validator`（348-356 行）替换视觉回落段（embed 段保留）：

```python
    @model_validator(mode="after")
    def _validate_summary_ratios(self) -> "BotConfig":
        if self.summary_keep_ratio > self.summary_trigger_ratio:
            raise ValueError("summary_keep_ratio must be <= summary_trigger_ratio")
        if "embed_base_url" not in self.model_fields_set and self.ollama_base_url:
            self.embed_base_url = self.ollama_base_url
        # 视觉已切 OpenAI 兼容：base_url / api_key 未设时回落主 LLM（同供应商零配置）
        if "vision_base_url" not in self.model_fields_set and self.llm_base_url:
            self.vision_base_url = self.llm_base_url
        if "vision_api_key" not in self.model_fields_set:
            self.vision_api_key = self.llm_api_key
        return self
```

- [ ] **Step 4: 更新 `.env-template`（否则 alias 一致性测试失败）**

`.env-template:51-55` 视觉段改为：

```
# --- Vision (OpenAI 兼容视觉 API，图片描述) ---
# BOT_VISION_ENABLED = 1
# BOT_VISION_MODEL = qwen-vl-max        # 供应商的 OpenAI 兼容视觉模型名
# BOT_VISION_BASE_URL = https://api.example.com/v1  # 视觉专用；未设置时回落 BASE_URL
# BOT_VISION_API_KEY = sk-xxx           # 视觉专用；未设置时回落 API_KEY
# BOT_VISION_MAX_IMAGES = 3
```

- [ ] **Step 5: 运行测试验证通过**

Run: `cd /f/PythonProject/qq-bot && uv run pytest tests/test_config.py -q`
Expected: PASS（含 `test_env_template_contains_all_env_aliases`、`test_every_field_has_env_alias`）。

- [ ] **Step 6: Commit**

```bash
cd /f/PythonProject/qq-bot
git add src/common/config.py tests/test_config.py .env-template
git commit -m "feat(config): 视觉新增 BOT_VISION_API_KEY，未设回落主 LLM base_url/api_key"
```

---

### Task 2: VisionService 改走 OpenAI 兼容 `/v1/chat/completions`

**Files:**
- Modify: `src/bot/core/vision/service.py:125-157`（describe / _download_base64 / _ollama_generate → _openai_generate）
- Modify: `tests/test_vision_service.py`（URL、payload 断言、新增鉴权用例）

**Interfaces:**
- Consumes: `BotConfig.vision_api_key`（Task 1 产出，可空）、`VisionService(base_url, model, api_key=None, prompt=VISION_PROMPT, timeout=60.0, max_images=3, http=None)`。
- Produces: `VisionService` 公开接口不变（`describe(src)->str`、`describe_many(srcs)->list[str]`、`close()`），`describe_image_node` 无感知；新增私有 `_openai_generate(data_url)->str`。

- [ ] **Step 1: 写失败测试（vision service）**

`tests/test_vision_service.py` 修改：

- 顶部常量 `GEN` 改 URL、`_svc` 增 `api_key` 参数：
```python
# 公网字面 IP：字面 IP 的 getaddrinfo 不查 DNS，避免测试慢/不稳，也不触发 SSRF 阻断
IMG = "http://1.2.3.4/download?appid=1&fileid=abc"
GEN = "http://localhost:11434/v1/chat/completions"
```

- `FakeClient.post` 把 `**kwargs`（含 headers）记进请求记录：
```python
    async def post(self, url, json=None, **kwargs):
        self.requests.append(("post", url, json, kwargs))
        if url not in self.responses:
            raise httpx.HTTPError(f"no response for {url}")
        return self.responses[url]
```

- `_svc` 与 `test_describe_downloads_and_generates` 整体替换：
```python
def _svc(client, max_images=3, api_key=None):
    return VisionService(base_url="http://localhost:11434", model="qwen3-vl:2b",
                         http=client, max_images=max_images, api_key=api_key)


def test_describe_downloads_and_generates():
    png = b"\x89PNG\r\n\x1a\n"
    client = FakeClient({
        IMG: FakeResponse(content=png),
        GEN: FakeResponse(json_data={"choices": [{"message": {"content": "一只猫坐在窗台上"}}]}),
    })
    svc = _svc(client)
    assert asyncio.run(svc.describe(IMG)) == "一只猫坐在窗台上"
    post = [r for r in client.requests if r[0] == "post"]
    assert len(post) == 1
    url, payload, kwargs = post[0][1], post[0][2], post[0][3]
    assert url == GEN
    assert payload["model"] == "qwen3-vl:2b"
    assert payload["stream"] is False
    assert payload["messages"][0] == {"role": "system", "content": VISION_PROMPT}
    assert payload["messages"][1]["content"][1] == {
        "type": "image_url",
        "image_url": {"url": f"data:image/jpeg;base64,{base64.b64encode(png).decode('ascii')}"},
    }
    assert "Authorization" not in (kwargs.get("headers") or {})


def test_describe_sends_bearer_token():
    png = b"\x89PNG\r\n\x1a\n"
    client = FakeClient({
        IMG: FakeResponse(content=png),
        GEN: FakeResponse(json_data={"choices": [{"message": {"content": "图"}}]}),
    })
    svc = _svc(client, api_key="sk-vision")
    asyncio.run(svc.describe(IMG))
    post = [r for r in client.requests if r[0] == "post"][0]
    assert post[3]["headers"]["Authorization"] == "Bearer sk-vision"


def test_describe_missing_choices_returns_empty():
    client = FakeClient({
        IMG: FakeResponse(content=b"data"),
        GEN: FakeResponse(json_data={"choices": []}),
    })
    svc = _svc(client)
    assert asyncio.run(svc.describe(IMG)) == ""
```

- 把 `test_describe_ollama_failure_returns_empty` 改名并换响应形状：
```python
def test_describe_api_failure_returns_empty():
    svc = _svc(FakeClient({
        IMG: FakeResponse(content=b"data"),
        GEN: FakeResponse(status=500),
    }))
    assert asyncio.run(svc.describe(IMG)) == ""
```

- `test_describe_many_caps_at_max_images` 与 `test_describe_many_partial_failure` 的 `GEN` 响应换成 OpenAI 形状：
```python
        GEN: FakeResponse(json_data={"choices": [{"message": {"content": "图"}}]}),
```
（两处 `{"response": "图"}` → `{"choices": [{"message": {"content": "图"}}]}`）

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /f/PythonProject/qq-bot && uv run pytest tests/test_vision_service.py -q`
Expected: FAIL —— payload 断言不匹配（旧实现发 `/api/generate`、body 无 `messages`、无 `api_key` 参数）。

- [ ] **Step 3: 实现 VisionService 改动**

`src/bot/core/vision/service.py`：

1. 模块 docstring 首段从「Ollama 视觉推理」改为「OpenAI 兼容视觉端点」。
2. `__init__` 增 `api_key` 参数：
```python
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        prompt: str = VISION_PROMPT,
        timeout: float = 60.0,
        max_images: int = 3,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.prompt = prompt
        self.timeout = timeout
        self.max_images = max_images
        self._owns_http = http is None
        # 推理保留 timeout 预算，连接阶段单独缩短到 10s（防被慢速连接卡死）
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(self.timeout, connect=10))
```
3. `describe` 调用链换成 data URL + OpenAI 生成：
```python
    async def describe(self, src: str) -> str:
        """下载一张图并返回描述；失败返回空串（不抛出）。"""
        try:
            image_data_url = await self._download_data_url(src)
            if not image_data_url:
                return ""
            return await self._openai_generate(image_data_url)
        except Exception:
            logger.warning("Vision describe failed for %s", src, exc_info=True)
            return ""
```
4. `_download_base64` 换成 `_download_data_url`（带 mime）：
```python
    async def _download_data_url(self, src: str) -> str:
        data, mime = await _fetch_image_bytes(self._http, src)
        return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
```
5. `_ollama_generate` 换成 `_openai_generate`：
```python
    async def _openai_generate(self, image_data_url: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "请描述这张图片。"},
                        {"type": "image_url", "image_url": {"url": image_data_url}},
                    ],
                },
            ],
            "stream": False,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        resp = await self._http.post(
            f"{self.base_url}/v1/chat/completions", json=payload, headers=headers
        )
        resp.raise_for_status()
        data = resp.json()
        try:
            return (data["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError):
            logger.warning("Unexpected OpenAI vision response shape: %s", data)
            return ""
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd /f/PythonProject/qq-bot && uv run pytest tests/test_vision_service.py tests/test_describe_image.py -q`
Expected: PASS（`test_describe_image.py` 用 `FakeVisionService`，确认未受影响）。

- [ ] **Step 5: Commit**

```bash
cd /f/PythonProject/qq-bot
git add src/bot/core/vision/service.py tests/test_vision_service.py
git commit -m "feat(vision): VisionService 改走 OpenAI 兼容 /v1/chat/completions"
```

---

### Task 3: `main.py` 传入 api_key + 空 base_url 防御

**Files:**
- Modify: `main.py:74-81`（VisionService 构造）
- Test: 无单测；跑全量测试 + import 冒烟。

**Interfaces:**
- Consumes: `VisionService(base_url, model, api_key=None, ...)`（Task 2）、`BotConfig.vision_api_key` / `vision_base_url`（Task 1，`vision_base_url` 可为 `None`）。
- Produces: 运行时 `vision_service` 实例或 `None`。

- [ ] **Step 1: 改 `main.py` 构造**

`main.py:74-81` 替换为：

```python
    vision_service = None
    if config.vision_enabled:
        if not config.vision_base_url:
            logger.warning("vision_enabled but vision_base_url is empty; disabling vision")
        else:
            vision_service = VisionService(
                base_url=config.vision_base_url,
                model=config.vision_model,
                api_key=config.vision_api_key,
                timeout=config.vision_timeout,
                max_images=config.vision_max_images,
            )
```

- [ ] **Step 2: import 冒烟 + 全量测试**

Run: `cd /f/PythonProject/qq-bot && uv run python -c "import main" && uv run pytest -q`
Expected: import 无异常；全量测试 PASS。

- [ ] **Step 3: Commit**

```bash
cd /f/PythonProject/qq-bot
git add main.py
git commit -m "feat(main): VisionService 传入 vision_api_key，空 base_url 时降级关闭"
```

---

## Self-Review

**1. Spec 覆盖（对照 chat 确认的设计）：**
- 配置新增 `BOT_VISION_API_KEY` + `vision_base_url` 回落主 LLM → Task 1 ✓
- 独立字段、未设回落主 LLM key → Task 1 的 `test_vision_url_and_key_fall_back_to_main_llm` ✓
- 直接替换 Ollama → Task 2 删除 `_ollama_generate`、新增 `_openai_generate` ✓
- `describe`/`describe_many` 接口不变、`download_images_as_data_urls` 不动 → Task 2 未改这两处 ✓
- main.py 传 `api_key` + 防御空 base_url → Task 3 ✓
- `.env-template` 同步 → Task 1 Step 4 ✓
- 降级行为不变（失败返回 "" → [图片] 占位符）→ Task 2 `test_describe_api_failure_returns_empty` ✓

**2. Placeholder 扫描：** 无 TBD/TODO；每个代码步骤都给了完整可粘贴内容。

**3. 类型一致性：**
- `VisionService.__init__` 签名在 Task 2 定义，Task 3 用关键字 `api_key=` 传参 ✓
- `BotConfig.vision_api_key` / `vision_base_url` 在 Task 1 定义（类型 `str | None`），Task 3 的 `if not config.vision_base_url` 防御匹配 ✓
- `_svc(client, api_key=...)` 与 `VisionService(... api_key=api_key)` 参数名一致 ✓
- `FakeClient.post` 记录元组变为 `("post", url, json, kwargs)`，Task 2 测试用 `post[0][1]`/`[2]`/`[3]` 解包，一致 ✓

**4. 已知边界：** `test_config.py` 的 `EXPECTED_DEFAULTS`/`ENV_SAMPLES` 与字段一一对应，Task 1 若漏改任一处，`test_every_field_has_env_alias` / `test_defaults_without_env_file` 会立刻红，TDD 循环自然兜住。
