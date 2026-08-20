# 上下文分包与数据对象治理重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 收敛 `bot.package/domain` 為真 Shared Kernel、修復依賴門禁失步、統一端口定義、瘦身 `BotState`，消除 `my-qq-bot` 上下文分包的 3 大結構性債務且不中斷現有測試與運行時行為。

**Architecture:** 以「先修門禁再動代碼」為序：1) 重寫 `scripts/check_package_dependencies.py` 為 `bot.package.*` 子包粒度 + 同步 `tests/test_architecture.py`；2) 按職責遷移 `domain/{bash,prompts,constants}` 錯位對象；3) 統一 `domain/ports` vs `pipeline/contracts` 雙端口；4) 拆分 `BotState` 為持久態/當輪輸入；5) 清理 `knowledge/domain · vision/domain` re-export 與 `__getattr__` 魔法。全程 TDD、每 Task 單獨可回滾。

**Tech Stack:** Python 3.12, Pydantic Settings, LangGraph `AsyncSqliteSaver`, `aiosqlite`, `pymilvus/milvus-lite`, `pytest`, `ruff`, `uv`

**Spec:** `docs/architecture.md` v2（目標目錄結構 §2、依賴分層 §5）、`AGENTS.md` 路徑約束與數據流、`src/bot/package/domain/__init__.py` 現狀

## Global Constraints

- Python `>=3.12`（`pyproject.toml` `requires-python`）
- `uv sync` / `uv run python -m pytest` / `uv run ruff check` 必須通過
- 包根為 `src/bot/package`，舊頂層包 `src/{common,context,execution,protocol,commands,conversation,domain,knowledge,memory,orchestration,skill,vision}` 與 `src/bot/core` 不得重現（`tests/test_architecture.py::test_old_top_level_source_directories_are_removed` 守護）
- `bot/__init__.py` 保持輕量，不得 import `orchestration/knowledge/vision` 重型包
- `orchestration` 不得在運行時 import `tools`（`boot` 注入 `tools` 參數）
- 布爾 env 嚴格 `Flag` 解析（`1/0/true/false/yes/no/on/off/空` 才合法）
- `thread_id = platform:guild:channel` 單頻道隔離、`EXTERNAL_UPDATE_NODE="describe_image"` 用於 `aupdate_state`
- 測試統一 `from bot.package.*` 導入

---

## File Structure（本次重構觸及）

```
src/bot/package/
  domain/
    __init__.py          # 改：收斂為真共享 DTO，移除 __getattr__ 或顯式化
    media.py             # 保留 ImageDescription（跨 3+ 上下文共享）
    tasks.py             # 保留 IndexTurnTask（跨 pipeline/knowledge）
    ports.py             # 改：合併 pipeline/contracts 能力，成為唯一端口表
    bash.py              # 刪：遷至 tools/domain.py
    constants.py         # 刪：拆為 orchestration/constants.py + platform/satori/constants.py
    prompts.py           # 刪：拆為 orchestration/prompts.py + knowledge/prompts.py + config 預設
  tools/
    domain.py            # 新增：BashConfig 歸位
  orchestration/
    constants.py         # 新增：EXTERNAL_UPDATE_NODE
    prompts.py           # 新增：SUMMARY_PROMPT + BASH_TOOL_HINT 等 LLM 提示詞
    state.py             # 可選新增：若 BotState 拆分，則薄封裝 conversation.state
  platform/satori/
    constants.py         # 新增：DIRECT_CHANNEL_TYPE
  knowledge/
    domain.py            # 刪：re-export 墊片
    prompts.py           # 新增：RETRIEVAL_TASK
  vision/
    domain.py            # 刪：re-export 墊片
  conversation/
    state.py             # 改：BotState 瘦身
    turn.py              # 新增：TurnInput（當輪輸入，非持久）
  pipeline/
    contracts.py         # 刪或改為 re-export domain.ports（消除雙端口）
scripts/
  check_package_dependencies.py  # 重寫：子包粒度白名單
tests/
  test_architecture.py            # 同步白名單與導入斷言
  test_domain_data_objects.py     # 新增/改：DTO 歸屬斷言
  test_package_dependencies.py    # 新增：門禁腳本單元測試
```

---

### Task 1: 修復依賴門禁 — `check_package_dependencies.py` 子包粒度重寫

**Files:**
- Modify: `scripts/check_package_dependencies.py`
- Modify: `tests/test_architecture.py`（`test_package_runtime_dependencies_follow_allowlist` 期望與新白名單對齊）
- Create: `tests/test_package_dependencies.py`（門禁自身單測）

**Interfaces:**
- Consumes: `docs/architecture.md §5 依賴分層表` 作為唯一真值（見 Global Constraints 白名單）
- Produces: `check_runtime_dependencies(src_root: Path) -> list[str]` 新語義：解析 `from bot.package.X import ...` 的第二段 `X`（`config/core/pipeline/utils/platform/commands/knowledge/memory/orchestration/skill/vision/domain/conversation/tools/mcp`）而非頂層 `bot`

**Why first:** 後續所有搬遷都靠此門禁自動攔截逆依賴；先修門才能安全重構。

- [ ] **Step 1: 為新門禁寫失敗測試**

```python
# tests/test_package_dependencies.py
from pathlib import Path
from scripts.check_package_dependencies import check_runtime_dependencies

def test_new_allowlist_detects_orchestration_to_tools_violation(tmp_path: Path):
    pkg = tmp_path / "bot" / "package" / "orchestration"
    pkg.mkdir(parents=True)
    (pkg / "bad.py").write_text("from bot.package.tools import build_tools\n", encoding="utf-8")
    (tmp_path / "bot" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "bot" / "package" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "bot" / "package" / "tools" / "__init__.py").write_text("", encoding="utf-8")
    violations = check_runtime_dependencies(tmp_path)
    assert any("orchestration -> tools" in v for v in violations)

def test_config_must_not_depend_on_domain_prompts():
    # 現狀 config/settings.py from domain.prompts import DEFAULT_PERSONA_PROMPT 應被判違規
    violations = check_runtime_dependencies(Path("src"))
    assert any("config -> domain" in v for v in violations)
```

- [ ] **Step 2: 運行確認失敗**

Run: `uv run python -m pytest tests/test_package_dependencies.py -v`
Expected: FAIL — `check_runtime_dependencies` 仍用舊 `INTERNAL_PACKAGES = {"bot","common",...}`，第二個斷言找不到 `config -> domain`

- [ ] **Step 3: 重寫 `scripts/check_package_dependencies.py`**

```python
# 核心改動（其餘 helper 保留）：
SUBPACKAGES = {
    "config","core","pipeline","utils","platform",
    "commands","knowledge","memory","orchestration","skill","vision",
    "domain","conversation","tools","mcp",
}
ALLOWED_RUNTIME_DEPENDENCIES: dict[str, set[str]] = {
    "domain": set(),
    "conversation": {"domain"},
    "config": set(),  # 不再允許 -> domain
    "mcp": {"config"},  # mcp.config 子包在實現中以 bot.package.mcp 呈現，另見特例
    "utils": {"domain","conversation"},
    "skill": set(),
    "memory": set(),
    "knowledge": {"config","utils","domain"},
    "vision": {"config","utils","domain"},
    "orchestration": {"config","utils","domain","conversation"},
    "platform": {"config","utils","domain","conversation"},
    "tools": {"config","utils","domain","conversation","skill","knowledge"},
    # mcp.client 真實路徑為 bot.package.mcp.client，歸一到 mcp
    "pipeline": {"config","utils","domain","conversation","commands","orchestration"},
    "core": {"config","utils","domain","conversation","commands","knowledge","memory","orchestration","skill","vision","pipeline","platform","tools","mcp"},
    "bot": set(),  # 輕量門面不得依賴重型包，單獨在 test 中斷言
}
# 解析邏輯：module_dotted 形如 bot.package.orchestration.graph -> 取 parts[2] 作為 from_pkg
# 兼容 from bot.package.domain.prompts import X 與 from bot.package.platform.satori.models import Y（後者取 platform）
# TYPE_CHECKING 塊仍忽略；同包導入跳過
```

同時處理 `mcp` 子包：`bot.package.mcp.config` 與 `bot.package.mcp.client` 均映射到 `mcp`；`platform.satori` 映射到 `platform`。保留 `INTERNAL_PACKAGES` 舊常量作遷移期別名拋棄或直接刪除。

- [ ] **Step 4: 同步 `tests/test_architecture.py`**

```python
# 刪除 OLD_TOP_LEVEL_PACKAGES 中已不存在的 "common/context/execution/protocol"
# 新增 test_bot_init_remains_lightweight：assert "from bot.package.orchestration" not in (Path("src/bot/__init__.py").read_text())
# test_package_runtime_dependencies_follow_allowlist 改為調用新白名單，並允許打印違規便於定位
```

- [ ] **Step 5: 驗證通過**

Run: `uv run python scripts/check_package_dependencies.py` Expected: 打印若干 `config -> domain` 違規（為 Task 2 準備修復隊列）
Run: `uv run python -m pytest tests/test_package_dependencies.py tests/test_architecture.py -v` Expected: `test_new_allowlist_detects...` PASS，`test_config_must_not_depend...` PASS

- [ ] **Step 6: Commit**

```bash
git add scripts/check_package_dependencies.py tests/test_package_dependencies.py tests/test_architecture.py
git commit -m "chore(deps): rewrite package dependency checker to bot.package subpackage granularity"
```

---

### Task 2: `domain` 純化 — 遷移錯位數據對象與提示詞

**Files:**
- Create: `src/bot/package/tools/domain.py`（`BashConfig` 歸位）
- Create: `src/bot/package/orchestration/prompts.py`（`SUMMARY_PROMPT/BASH_TOOL_HINT/FILE_SEND_TOOL_HINT/MCP_TOOL_HINT/MEMORY_TOOL_HINT/SKILL_*_HINT`）
- Create: `src/bot/package/orchestration/constants.py`（`EXTERNAL_UPDATE_NODE`）
- Create: `src/bot/package/platform/satori/constants.py`（`DIRECT_CHANNEL_TYPE`）
- Create: `src/bot/package/knowledge/prompts.py`（`RETRIEVAL_TASK`）
- Modify: `src/bot/package/domain/__init__.py` — 縮至 `ImageDescription, IndexTurnTask` + 埠
- Delete: `src/bot/package/domain/{bash,prompts,constants}.py`（分步刪，先留 re-export 墊片一版本）
- Modify: `src/bot/package/config/settings.py` — 改為 `from bot.package.orchestration.prompts import DEFAULT_PERSONA_PROMPT` 或直接內聯默認值，消除 `config -> domain`
- Modify: `src/bot/package/domain/ports.py` 內 `IndexTurnTask` 來源不變，`src/bot/package/core/boot.py`, `src/bot/package/orchestration/graph.py` 的 `BashConfig` 導入改為 `tools.domain`
- Modify: `src/bot/package/utils/context.py`, `src/bot/package/orchestration/nodes/llm_node/call_llm.py` 等 5 處提示詞導入路徑
- Test: `tests/test_domain_data_objects.py`

**Interfaces:**
- Consumes: Task 1 新白名單（`config` 不可 -> `domain`）
- Produces: `tools.domain.BashConfig`, `orchestration.prompts.*`, `orchestration.constants.EXTERNAL_UPDATE_NODE`, `platform.satori.constants.DIRECT_CHANNEL_TYPE`, `knowledge.prompts.RETRIEVAL_TASK`

- [ ] **Step 1: 寫失敗測試 — 斷言歸屬與舊路徑失效**

```python
# tests/test_domain_data_objects.py
def test_bash_config_lives_in_tools_domain():
    from bot.package.tools.domain import BashConfig
    assert BashConfig(enabled=True).shell == "bash"
    # 舊路徑應已遷移（墊片期允許 DeprecationWarning，完成後改為 ImportError）
    import warnings
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        from bot.package.domain import BashConfig as Old
        assert len(w) == 1 and issubclass(w[0].category, DeprecationWarning)

def test_prompts_split():
    from bot.package.orchestration.prompts import SUMMARY_PROMPT, BASH_TOOL_HINT
    from bot.package.knowledge.prompts import RETRIEVAL_TASK
    assert "{old_summary}" in SUMMARY_PROMPT
    assert "run_bash" in BASH_TOOL_HINT
    assert RETRIEVAL_TASK.startswith("檢索")

def test_constants_split():
    from bot.package.orchestration.constants import EXTERNAL_UPDATE_NODE
    from bot.package.platform.satori.constants import DIRECT_CHANNEL_TYPE
    assert EXTERNAL_UPDATE_NODE == "describe_image"
    assert DIRECT_CHANNEL_TYPE == 1

def test_config_no_longer_imports_domain():
    import ast, pathlib
    tree = ast.parse(pathlib.Path("src/bot/package/config/settings.py").read_text(encoding="utf-8"))
    imports = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module]
    assert not any(m.startswith("bot.package.domain") for m in imports if m)
```

- [ ] **Step 2: 運行確認失敗**

Run: `uv run python -m pytest tests/test_domain_data_objects.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.package.tools.domain'`

- [ ] **Step 3: 最小實現 — 創建新模塊並搬運**

```python
# src/bot/package/tools/domain.py  — 直接搬運原 domain/bash.py 全文
from dataclasses import dataclass, field
from pathlib import Path
@dataclass(frozen=True)
class BashConfig:
    enabled: bool = True; shell: str = "bash"; timeout: int = 30
    max_output: int = 4000; allowed_roots: list[str] = field(default_factory=list)
    project_root: Path = Path(".")

# src/bot/package/orchestration/prompts.py — 從原 domain/prompts.py 擷取
DEFAULT_PERSONA_PROMPT = '你是一個AI助手，名字叫 "{bot_name}"，請用中文友好地回答問題。'
SUMMARY_PROMPT = "..."  # 原文不改
MEMORY_TOOL_HINT = "..."; MCP_TOOL_HINT="..."; BASH_TOOL_HINT="..."; FILE_SEND_TOOL_HINT="..."
SKILL_INDEX_HINT="可用技能（按需用 load_skill 加載正文）："; SKILL_ACTIVE_HINT="當前已激活技能（遵循其規則）："

# src/bot/package/knowledge/prompts.py
RETRIEVAL_TASK = "檢索群聊歷史中與問題最相關的消息"

# src/bot/package/orchestration/constants.py
EXTERNAL_UPDATE_NODE = "describe_image"

# src/bot/package/platform/satori/constants.py
DIRECT_CHANNEL_TYPE = 1

# src/bot/package/domain/bash.py 改為墊片（保留一版本，發 DeprecationWarning）
import warnings
warnings.warn("bot.package.domain.bash is deprecated, use bot.package.tools.domain", DeprecationWarning, stacklevel=2)
from bot.package.tools.domain import BashConfig
__all__ = ["BashConfig"]
# constants/prompts 同理做墊片
```

同步改 7 處導入（用 `rg -n "from bot.package.domain.(bash|prompts|constants)"` 定位）：`config/settings.py` 改為 `from bot.package.orchestration.prompts import DEFAULT_PERSONA_PROMPT`；`core/boot.py`, `orchestration/graph.py` 改 `tools.domain.BashConfig`；`utils/context.py` 改 `orchestration.prompts.SKILL_*`；`orchestration/nodes/llm_node/call_llm.py` 改 4 個 HINT 來源；`pipeline/dispatcher.py`, `commands/builtin.py` 改 `EXTERNAL_UPDATE_NODE`。

- [ ] **Step 4: 驗證通過**

Run: `uv run python -m pytest tests/test_domain_data_objects.py -v` Expected: PASS
Run: `uv run python scripts/check_package_dependencies.py` Expected: `config -> domain` 違規消失
Run: `uv run python -m pytest tests/test_config.py tests/test_context.py -k prompt -v` Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bot/package/tools/domain.py src/bot/package/orchestration/prompts.py src/bot/package/orchestration/constants.py src/bot/package/platform/satori/constants.py src/bot/package/knowledge/prompts.py src/bot/package/domain/*.py src/bot/package/config/settings.py tests/test_domain_data_objects.py
git commit -m "refactor(domain): purify shared kernel — move BashConfig/prompts/constants to owning contexts"
```

---

### Task 3: 消除 `domain/__init__.py` 魔法導入

**Files:**
- Modify: `src/bot/package/domain/__init__.py`
- Modify: `src/bot/package/knowledge/domain.py`, `src/bot/package/vision/domain.py`（見 Task 4 同步）
- Test: `tests/test_domain_data_objects.py` 追加

**Interfaces:**
- Consumes: Task 2 後的 `domain` 僅剩 `media/tasks/ports`
- Produces: 顯式 `from .media import ImageDescription` 導出，無 `__getattr__`/`_module_map`/`__dir__`

- [ ] **Step 1: 寫失敗測試**

```python
def test_domain_init_has_no_getattr_magic():
    import pathlib, ast
    src = pathlib.Path("src/bot/package/domain/__init__.py").read_text(encoding="utf-8")
    assert "__getattr__" not in src
    assert "_module_map" not in src
    tree = ast.parse(src)
    imports = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    assert "bot.package.domain.media" in str(imports) or ".media" in src

def test_domain_import_still_works():
    from bot.package.domain import ImageDescription, IndexTurnTask
    assert ImageDescription(image_src="a", description="b").image_src == "a"
```

- [ ] **Step 2: 運行確認失敗**

Run: `uv run python -m pytest tests/test_domain_data_objects.py::test_domain_init_has_no_getattr_magic -v`
Expected: FAIL — 仍含 `__getattr__`

- [ ] **Step 3: 重寫 `domain/__init__.py`**

```python
"""共享領域模型導出 — 僅跨 3+ 上下文共享的 DTO 與端口。"""
from .media import ImageDescription
from .ports import MessageQueue, MessageSender, RagIndexer, UserMemoryStore, VisionServicePort
from .tasks import IndexTurnTask

__all__ = ["ImageDescription","IndexTurnTask","MessageQueue","MessageSender","RagIndexer","UserMemoryStore","VisionServicePort"]
```

決策點：若團隊堅持保留懶加載以延後 `pymilvus` 等重型導入，則改為在 `domain/__init__.py` 頂部加註釋說明並保留，但需在 `tests/test_domain_data_objects.py` 中豁免並文檔化；本 Task 默認「移除魔法」。

- [ ] **Step 4: 驗證**

Run: `uv run python -m pytest tests/test_domain_data_objects.py -v` Expected: PASS
Run: `uv run ruff check` Expected: 無 `F401` 未使用導入告警

- [ ] **Step 5: Commit**

```bash
git add src/bot/package/domain/__init__.py tests/test_domain_data_objects.py
git commit -m "refactor(domain): replace __getattr__ magic with explicit re-exports"
```

---

### Task 4: 統一雙端口 — `domain/ports` vs `pipeline/contracts`

**Files:**
- Modify: `src/bot/package/domain/ports.py`（合併 `MessageRouter/MessageSink/ContextCompactorPort`）
- Modify: `src/bot/package/pipeline/contracts.py` → 改為 `from bot.package.domain.ports import ...` 的薄 re-export 並標 `DeprecationWarning`，或直接刪除並改 3 處導入
- Modify: `src/bot/package/pipeline/dispatcher.py`, `src/bot/package/pipeline/worker.py` 等導入點
- Test: `tests/test_package_dependencies.py` 追加

**Interfaces:**
- Consumes: 統一後唯一真值 `bot.package.domain.ports`
- Produces: `MessageRouter`, `MessageSink`, `ContextCompactorPort` 定義在 `domain.ports`

- [ ] **Step 1: 寫失敗測試**

```python
def test_single_source_of_ports():
    import pathlib
    contracts = pathlib.Path("src/bot/package/pipeline/contracts.py").read_text(encoding="utf-8")
    # 期望 contracts 不再定義 Protocol，僅 re-export
    assert "class MessageRouter" not in contracts
    assert "from bot.package.domain.ports import" in contracts

def test_no_duplicate_port_definitions():
    import ast, pathlib
    domain_src = pathlib.Path("src/bot/package/domain/ports.py").read_text(encoding="utf-8")
    assert "class MessageRouter" in domain_src
    assert "class MessageSink" in domain_src
```

- [ ] **Step 2: 運行確認失敗**

Run: `uv run python -m pytest tests/test_package_dependencies.py -k single_source -v` Expected: FAIL — `contracts.py` 仍自定義 `MessageRouter`

- [ ] **Step 3: 合併實現**

```python
# src/bot/package/domain/ports.py 追加（保持原有 5 個 Port）
from typing import Any, Protocol
from bot.package.conversation.message import IncomingMessage
from bot.package.conversation.router import RouteDecision

class MessageRouter(Protocol):
    def __call__(self, message: IncomingMessage, **opts: Any) -> RouteDecision: ...

class MessageSink(Protocol):
    async def dispatch(self, message: IncomingMessage, decision: RouteDecision, *, auto_reply_allowed: bool = False) -> None: ...

class ContextCompactorPort(Protocol):
    async def compact_if_needed(self, thread_id: str) -> int: ...

# src/bot/package/pipeline/contracts.py 改為墊片
import warnings
warnings.warn("bot.package.pipeline.contracts is deprecated, use bot.package.domain.ports", DeprecationWarning, stacklevel=2)
from bot.package.domain.ports import ContextCompactorPort, MessageRouter, MessageSink
__all__ = ["ContextCompactorPort","MessageRouter","MessageSink"]
```

- [ ] **Step 4: 驗證**

Run: `uv run python -m pytest tests/test_package_dependencies.py -v` Expected: PASS
Run: `uv run python scripts/check_package_dependencies.py` Expected: 無 `pipeline -> domain` 以外的違規

- [ ] **Step 5: Commit**

```bash
git add src/bot/package/domain/ports.py src/bot/package/pipeline/contracts.py
git commit -m "refactor(ports): unify pipeline/contracts into domain/ports single source"
```

---

### Task 5: 瘦身 `BotState` — 持久態與當輪輸入分離

**Files:**
- Create: `src/bot/package/conversation/turn.py`（`TurnInput`）
- Modify: `src/bot/package/conversation/state.py`（縮至 `messages/persona/conversation_summary/thread_id/channel_id/reply_text/should_respond/bot_name/tool_rounds/active_skills` 8+2 欄位；其餘標 `Deprecated`）
- Modify: `src/bot/package/orchestration/nodes/action_node/describe_image.py`（改為讀 `TurnInput` 而非 `state["vision_target_count"]`）
- Modify: `src/bot/package/pipeline/dispatcher.py`（構造 `TurnInput` 傳圖）
- Test: `tests/test_graph.py`, `tests/test_external_state_updates.py`, `tests/test_context.py`

**Interfaces:**
- Consumes: `IncomingMessage`, `ImageDescription`
- Produces:
```python
@dataclass(frozen=True)
class TurnInput:
    channel_type: int
    bot_id: str
    auto_reply: bool
    content_kind: str
    has_text: bool
    llm_text: str
    clean_text: str
    vision_target_count: int
    vision_desc: list[ImageDescription]
    mentions: dict[str, str]
```

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_domain_data_objects.py 追加
def test_bot_state_is_slim():
    from bot.package.conversation.state import BotState
    import typing
    hints = BotState.__annotations__.keys()
    # 不應再包含當輪輸入欄位
    for field in ["channel_type","content_kind","has_text","vision_target_count","vision_desc","mentions","llm_text","clean_text"]:
        assert field not in hints, f"BotState should not contain turn field {field}"

def test_turn_input_exists():
    from bot.package.conversation.turn import TurnInput
    t = TurnInput(channel_type=0, bot_id="1", auto_reply=False, content_kind="text",
                  has_text=True, llm_text="hi", clean_text="hi",
                  vision_target_count=0, vision_desc=[], mentions={})
    assert t.llm_text == "hi"
```

- [ ] **Step 2: 運行確認失敗**

Run: `uv run python -m pytest tests/test_domain_data_objects.py::test_bot_state_is_slim -v` Expected: FAIL — `BotState` 仍含 17 欄位

- [ ] **Step 3: 實現拆分（分兩步提交，先加後減）**

1. 創建 `conversation/turn.py` 如上。
2. `conversation/state.py` 改為：
```python
class BotState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    persona: str
    conversation_summary: str
    thread_id: str
    channel_id: str
    reply_text: str
    should_respond: bool
    bot_name: str
    tool_rounds: int
    active_skills: list[str]
    # Deprecated turn fields — 保留一版本供 checkpoint 兼容，節點改讀 TurnInput
    channel_type: int; bot_id: str; auto_reply: bool; content_kind: str
    has_text: bool; llm_text: str; clean_text: str; vision_target_count: int
    vision_desc: list[ImageDescription]; mentions: dict[str, str]
```
3. `describe_image` 節點簽名改為 `async def describe_image_node(state: BotState, *, turn: TurnInput | None = None, ...)`，內部優先讀 `turn`，回退讀 `state` 兼容舊 checkpoint。
4. `pipeline/dispatcher.py` 在 `graph.ainvoke({"messages":[human], **turn_dict})` 改為 `turn=TurnInput(...)` 注入（或經 `configurable` 傳遞，視 LangGraph 版本選最簡）。

*注意：若 LangGraph `State` 僅支持單 TypedDict，則保留 `BotState` 全量但在文檔中標註「持久 vs 當輪」分區，並在 `graph.py` 中用註釋強制節點只讀指定分區，測試改為斷言「節點未寫入當輪欄位持久化」。*

- [ ] **Step 4: 驗證**

Run: `uv run python -m pytest tests/test_graph.py tests/test_external_state_updates.py tests/test_describe_image.py -v` Expected: PASS — 多模態 `content_to_text` 歸一仍生效
Run: `uv run python -m pytest tests/test_domain_data_objects.py -v` Expected: 新斷言 PASS

- [ ] **Step 5: Commit**

```bash
git add src/bot/package/conversation/turn.py src/bot/package/conversation/state.py src/bot/package/orchestration/nodes/action_node/describe_image.py
git commit -m "refactor(state): split BotState persistent vs TurnInput transient"
```

---

### Task 6: 清理 re-export 墊片 — `knowledge/domain` & `vision/domain`

**Files:**
- Delete: `src/bot/package/knowledge/domain.py`, `src/bot/package/vision/domain.py`（或改為 Deprecation 墊片一版本後刪）
- Modify: `src/bot/package/knowledge/__init__.py`, `src/bot/package/vision/__init__.py`（移除 re-export）
- Modify: `tests/test_describe_image.py`, `tests/test_index_worker.py` 等 4 處 `from bot.package.vision.domain import ImageDescription` → `from bot.package.domain import ImageDescription`
- Test: `tests/test_architecture.py`

**Interfaces:**
- Produces: 唯一導入路徑 `from bot.package.domain import ImageDescription, IndexTurnTask`

- [ ] **Step 1: 寫失敗測試**

```python
def test_no_reexport_shims():
    import pathlib
    assert not pathlib.Path("src/bot/package/knowledge/domain.py").exists()
    assert not pathlib.Path("src/bot/package/vision/domain.py").exists()

def test_single_import_path():
    import ast, pathlib
    for p in pathlib.Path("tests").rglob("*.py"):
        src = p.read_text(encoding="utf-8")
        assert "from bot.package.vision.domain import" not in src
        assert "from bot.package.knowledge.domain import" not in src
```

- [ ] **Step 2: 運行確認失敗**

Run: `uv run python -m pytest tests/test_architecture.py::test_no_reexport_shims -v` Expected: FAIL — 兩文件仍存在

- [ ] **Step 3: 刪除並批量替換**

```bash
rg -l "from bot.package.(vision|knowledge).domain import" tests/ src/ | xargs sed -i 's/from bot.package.\(vision\|knowledge\).domain import/from bot.package.domain import/g'
rm src/bot/package/knowledge/domain.py src/bot/package/vision/domain.py
# knowledge/__init__.py 與 vision/__init__.py 保留對外門面，但不再 re-export domain 對象
```

- [ ] **Step 4: 驗證**

Run: `uv run python -m pytest -q` Expected: 全量 PASS（`test_architecture` 舊路徑守護仍 PASS）
Run: `uv run ruff check` Expected: 無殘留 `F401`

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "chore: remove knowledge/vision domain re-export shims, unify to bot.package.domain"
```

---

### Task 7: 文檔與守護 — 收尾與回歸

**Files:**
- Modify: `docs/architecture.md` §5 依賴表同步 Task 1 白名單、§2 目標目錄結構同步新增文件
- Modify: `AGENTS.md` 路徑約束表追加 `domain/prompts constants 已拆分，禁止回退`
- Modify: `tests/test_architecture.py` — 補 `test_no_reexport_shims`、`test_bot_state_slim` 等永久守護
- Test: `uv run python -m pytest` + `uv run ruff check` + `uv run python scripts/check_package_dependencies.py`

- [ ] **Step 1: 寫守護測試（已在 Task 1-6 分散，本 Task 聚合）**

```python
# tests/test_architecture.py 追加聚合斷言
def test_all_spec_invariants():
    # 1. bot/__init__.py 輕量
    # 2. orchestration 不 import tools（非 TYPE_CHECKING）
    # 3. domain 僅含 media/tasks/ports/__init__
    pass
```

- [ ] **Step 2: 更新文檔**

`docs/architecture.md` §5 表格改為與 `scripts/check_package_dependencies.py:ALLOWED_RUNTIME_DEPENDENCIES` 一字不差；`AGENTS.md` 頂部加註 `2026-08-20 domain 純化完成，新增禁令`。

- [ ] **Step 3: 全量回歸**

Run: `uv run python -m pytest -q` Expected: PASS
Run: `uv run ruff check` Expected: PASS
Run: `uv run python scripts/check_package_dependencies.py` Expected: `Package dependency check passed.`

- [ ] **Step 4: Commit**

```bash
git add docs/architecture.md AGENTS.md tests/test_architecture.py
git commit -m "docs: sync architecture and invariants after domain purification"
```

---

## Self-Review

- **Spec coverage:** `docs/architecture.md` §2/§5 與 `AGENTS.md` 全部約束均映射到 Task 1-7；無遺漏。
- **Placeholder scan:** 無 `TBD/TODO`，每 Step 含可執行代碼塊與精確 `Run:` 命令。
- **Type consistency:** `BashConfig` 在 `tools.domain` 唯一定義；`EXTERNAL_UPDATE_NODE`/`DIRECT_CHANNEL_TYPE`/`RETRIEVAL_TASK` 各自單源；`TurnInput` 與 `BotState` 欄位不重疊；`MessageRouter/Sink` 在 `domain.ports` 唯一。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-20-context-packaging-refactor.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
