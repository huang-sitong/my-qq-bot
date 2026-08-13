# bash 工具（run_bash）— 设计文档

日期：2026-08-11
状态：已确认（方案 A：自写纯函数工具）

## 目标

新增 `run_bash` 工具，让 LLM 在 bot 宿主（Windows 11 + Git Bash）上执行 bash 命令。主要用途：**运行 skill 里的脚本 + skill 内环境配置**（装依赖 / 建虚拟环境 / 设环境变量等）。工具与 skill 解耦（LLM 传 cwd），skill 正文自述脚本路径与执行步骤。**不设 admin 门禁**（放开），安全边界由工具层护栏兜底。

## 决策记录

| 决策 | 结论 | 理由 |
|---|---|---|
| 用途 | 跑 skill 脚本 + skill 内环境配置 | 用户确认；非 admin 运维场景 |
| 权限 | 放开，不设 admin 门禁 | 用户确认；护栏在工具层兜底 |
| shell | Git Bash（`bash -c`，non-login） | 用户确认；机器已装，贴合 bash 语义 |
| cwd 绑定 | LLM 传 cwd | 用户确认；工具与 skill 解耦，skill 正文自述路径 |
| 实现方案 | 自写纯函数工具（`asyncio.subprocess`） | 护栏（危险拦截 / cwd 白名单）要引用 bot 侧路径与正则，MCP server 与 langchain `ShellTool` 均不合身 |
| 默认开关 | `BOT_BASH_ENABLED=1` 默认开 | 用户确认放开；护栏兜底 |
| `curl|sh` 拦截 | 默认拦 | 常见恶意载荷；放行需改 `DANGEROUS_PATTERNS` 常量（见护栏） |
| 会话持久 | 每次调用新进程，无 shell 会话状态 | 环境配置靠文件系统持久（venv / 依赖文件），不靠 shell 变量 |

## 架构

```
BOT_BASH_* (env) → config.bash_* (BotConfig)
        ▼
create_graph 组装 BashConfig（frozen dataclass，含 project_root）
        ▼
build_tools(bash_config=...) → _make_bash_tool → run_bash (BaseTool)
        ▼
call_llm_node(use_bash=True) → system 注入 BASH_TOOL_HINT
        ▼
LLM tool_calls → ToolNode 执行 run_bash（三道闸）→ 回环 call_llm
```

执行路径（纯函数内三道闸，按序执行）：

```
run_bash(command, cwd)
  ① DANGEROUS_PATTERNS 正则黑名单 → 命中返回「已拦截」不执行
  ② cwd resolve() 后校验 ∈ {project_root} ∪ allowed_roots → 越界返回提示
  ③ asyncio.wait_for(proc.communicate(), timeout) → 超时杀进程树返回「命令超时」
     stdout+stderr 合并 → 截断到 max_output → 「退出码 N」+ 输出
```

## 改动清单

### 1. `bot/core/tools/run_bash.py`（新文件，纯函数 + 配置形状）

```python
@dataclass(frozen=True)
class BashConfig:
    """run_bash 工具配置（graph.py 从 BotConfig 组装，经闭包绑定进工具）。"""
    enabled: bool
    shell: str = "bash"            # Git Bash 可执行名或完整路径
    timeout: int = 30              # 秒
    max_output: int = 4000         # 字符
    allowed_roots: list[str] = field(default_factory=list)  # 空 → 仅项目根
    project_root: Path = Path(".") # graph.py 从 __file__.parents[2] 解析
```

- `DANGEROUS_PATTERNS`：正则黑名单常量（防御性深水，非完整保证；LLM 与 skill 内容才是真正信任边界）。示例类别：
  - 递归删根/家目录：`rm -rf /`、`rm -rf ~`、`rm -fr (/\b|~)`
  - 磁盘写：`mkfs`、`dd if=`（裸设备）
  - 关停机：`shutdown` / `reboot` / `poweroff` / `halt`（词边界）
  - bot 自身配置：`> \.env`、`rm (\.env|.*\.env)`（截断/删除环境配置）
  - 管道到 shell：`curl .*\| *(ba)?sh`、`wget .*\| *(ba)?sh`
- `_is_blocked(command) -> str | None`：命中返回匹配的模式文本（供返回文案）；未命中返回 None
- `_resolve_cwd(cwd, project_root, allowed_roots) -> Path`：空 cwd → project_root；相对路径 → `(project_root / cwd).resolve()`；绝对路径 → `Path(cwd).resolve()`。`resolve()` 处理 `..`/符号链接逃逸
- `async def run_bash(command, cwd="", *, cfg: BashConfig) -> str`：
  - ① `_is_blocked` → `已拦截：命令命中危险模式 <pattern>，不予执行。`
  - ② cwd 越界 → `工作目录 <path> 不在允许的根目录内。允许：<roots>`
  - ③ `asyncio.create_subprocess_exec(cfg.shell, "-c", command, cwd=str(cwd), stdout=PIPE, stderr=STDOUT)`——**cwd 用 subprocess 参数设置，不拼进命令串**（规避 MSYS 路径 munging）
  - 解码：先 UTF-8（`errors="replace"`），结果含 `�` 则改 GBK（中文 Windows 常见输出编码）
  - 超时：`asyncio.wait_for(proc.communicate(), timeout)`，`TimeoutError` → 杀进程树（Windows 用 `taskkill /T /F /PID` 或 `proc.kill()` 回收子进程）→ `命令超时（> N 秒），已终止。`
  - 截断：合并输出超过 `max_output` 字符 → 截断 + 附 `\n…（输出已截断）`
  - 返回：`退出码: {rc}\n{输出}`；rc=0 且无输出 → `命令执行成功（无输出）`

### 2. `bot/core/tools/factory.py`

- `build_tools` 新增参数 `bash_config: BashConfig | None = None`；`bash_config.enabled` 为真时追加 `_make_bash_tool(bash_config)`
- `_make_bash_tool`：`StructuredTool.from_function(coroutine=_run, name="run_bash", description=BASH_TOOL_DESCRIPTION)`；`_run(command, cwd="")` 闭包绑定 cfg，`except Exception → "工具执行失败。"`（与现有工具一致；护栏拦截/超时/cwd 越界返回具体文案，不走降级）

工具描述（LLM 可见）：
```
在 bot 宿主上执行 bash 命令（Git Bash，Windows）。主要用于运行技能（skill）中的脚本、
配置技能所需环境（安装依赖/创建虚拟环境/设置环境变量等）。
- command：要执行的 bash 命令字符串（每次调用独立新 shell，cd/export 不跨调用保持）
- cwd：工作目录（绝对路径；留空为项目根目录）
- 工作目录仅限白名单根目录内；危险命令会被拦截；输出截断；超时退出。
- 返回「退出码 N」+ 输出；退出码非 0 表示失败，可调整命令重试。
```

### 3. `common/prompts.py`

新增 `BASH_TOOL_HINT`（对齐 MCP_TOOL_HINT 模式，`call_llm` 在 `use_bash` 时注入）：
```
你可以用 run_bash 在服务器上执行 bash 命令（Git Bash，Windows）。
- 主要用于运行技能（skill）中的脚本、配置技能所需环境（安装依赖/创建虚拟环境/设置环境变量等）。
- 技能正文会说明脚本路径与执行步骤，按正文在对应目录（cwd）下执行。
- 工作目录仅限白名单根目录内；危险命令会被拦截；输出截断；超时退出。
- 返回「退出码 N」+ 输出；退出码非 0 表示失败，可调整命令重试。
```

### 4. `common/config.py`

Commands 段之后新增小节：

```python
# --- Bash 工具（skill 脚本执行；Git Bash） ---
bash_enabled: Flag = Field(default=True, validation_alias="BOT_BASH_ENABLED")
bash_shell: str = Field(default="bash", validation_alias="BOT_BASH_SHELL")
bash_timeout: int = Field(default=30, gt=0, validation_alias="BOT_BASH_TIMEOUT")
bash_max_output: int = Field(default=4000, ge=0, validation_alias="BOT_BASH_MAX_OUTPUT")
bash_allowed_roots: Annotated[list[str], NoDecode] = Field(
    default_factory=list, validation_alias="BOT_BASH_ALLOWED_ROOTS",
)
```

- 抽取共享助手 `_parse_comma_list(value) -> list[str]`（逗号切分 + 保序去重），`_parse_admin_ids` 与 `bash_allowed_roots` 校验共用——消除重复且零行为变更（admin 校验逻辑原样搬进助手）
- `bash_allowed_roots`：空 → 仅项目根；非空 → **项目根恒隐式加入** + 配置项扩展
- 严格布尔 `Flag` fail-fast 语义与其它布尔一致

### 5. `bot/core/graph.py`

- `create_graph` 内组装 `BashConfig(enabled=config.bash_enabled, shell=config.bash_shell, timeout=config.bash_timeout, max_output=config.bash_max_output, allowed_roots=config.bash_allowed_roots, project_root=Path(__file__).resolve().parents[2])`
- `build_tools(..., bash_config=bash_config)`；`use_bash = bash_config.enabled` 传给 `call_llm_node` 的 partial

### 6. `bot/core/nodes/llm_node/call_llm.py`

- 新增参数 `use_bash: bool = False`；`if use_bash: system_msgs.append(SystemMessage(content=BASH_TOOL_HINT))`（对齐 `use_mcp` 分支）

### 7. `bot/handler.py`

无需改动——`run_bash` 是普通工具，随 `build_tools` 进图，命令模块（图外斜杠指令）不涉及。

## 错误处理

| 场景 | 返回 | 说明 |
|---|---|---|
| 危险命令命中 | `已拦截：命令命中危险模式 <pattern>` | 具体文案，LLM 可调整；不走 ToolNode 降级 |
| cwd 越界 | `工作目录 <path> 不在允许的根目录内` | 具体文案；`resolve()` 防 `..`/符号链接 |
| 超时 | `命令超时（> N 秒），已终止。` | 杀进程树回收子进程 |
| bash 不存在 / subprocess 崩溃 | `工具执行失败。` | factory 层 `except Exception` 降级，与现有工具一致 |
| 退出码非 0 | `退出码: N` + 输出 | LLM 据此调整命令重试 |

## 边界确认

- **每次调用独立新进程**：`cd` / `export` 不跨调用保持。环境配置依赖文件系统持久（venv 目录 / 依赖文件落盘），脚本每次用绝对路径或传 cwd——工具描述与 HINT 均已注明
- **编码**：中文 Windows 下命令可能输出 GBK，UTF-8 解码回落 GBK（含替换符则重试）
- **并发**：同一 thread 串行（worker 锁），多 thread 各起独立子进程，无共享状态
- **prompt injection**：skill 正文 / 群消息可能诱导 LLM 执行 skill 之外的命令；护栏（危险拦截 + cwd 白名单）是最后一道，真正信任边界是 skill 内容与 LLM 行为——文档注明，不做过载承诺
- **bash 未安装 / BOT_BASH_SHELL 配错**：`create_subprocess_exec` 抛 `FileNotFoundError` → 降级「工具执行失败。」，不崩 bot
- **长输出**：截断到 `max_output`，防撑爆上下文
- **`curl|sh` 拦截**：默认拦；需放行的合法安装脚本由用户自行在 `DANGEROUS_PATTERNS` 里调整（配置代码内，非 env）
- `bash -c` 为 non-login：不 source profile，PATH 来自 bot 进程环境；如 skill 需要登录 PATH，后续可加 `BOT_BASH_LOGIN` 开关（非 MVP）

## 测试

- `tests/test_run_bash.py`（不真跑 Git Bash，monkeypatch `asyncio.create_subprocess_exec`，对齐 `test_tool_node.py` mock 风格）：
  - 危险模式命中：`DANGEROUS_PATTERNS` 逐条注入 → 返回拦截文案，且**未 spawn 子进程**（patch 后断言不调用）
  - cwd 越界：`..` 逃逸 / 白名单外绝对路径 → 返回越界提示
  - cwd 为空 → 默认 project_root
  - 超时：假 subprocess sleep → `wait_for` 触发 → 返回超时文案
  - 截断：假 subprocess 输出超 `max_output` → 截断 + 标注
  - 退出码：rc=1 → 输出含「退出码: 1」
  - 编码回落：假 subprocess 返回 GBK 字节 → 正确解码
- `tests/test_tools_factory.py`：`build_tools(bash_config=enabled)` 含 `run_bash`；`None`/disabled 不含
- `tests/test_config.py`：`BOT_BASH_*` 解析（enabled flag / timeout 非法抛 ValidationError / allowed_roots 逗号切分）
- `tests/test_call_llm_node.py`：`use_bash=True` → system 含 `BASH_TOOL_HINT`

## 文档

- `.env-template`：加 bash 小节注释（`BOT_BASH_ENABLED`/`BOT_BASH_SHELL`/`BOT_BASH_TIMEOUT`/`BOT_BASH_MAX_OUTPUT`/`BOT_BASH_ALLOWED_ROOTS`）
- `CLAUDE.md`：tools 段加 `run_bash`（用途 + 三道闸护栏 + 会话不持久 gotcha）

## 非目标

- 不做 admin 门禁（确认放开）
- 不做命令白名单（用户选危险拦截而非白名单）
- 不做沙箱（E2B / Docker / firejail）——YAGNI
- 不改 skill loader（不解析脚本字段；skill 正文自述脚本路径）
- 不做持久化 shell session / 会话状态
- 不做独立文件读写工具（经 bash 命令间接实现）
- 不做 PowerShell / cmd 支持（仅 Git Bash）
