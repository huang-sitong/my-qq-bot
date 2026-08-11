# bot/core/tools/run_bash.py
"""run_bash 工具纯函数：在 bot 宿主（Windows + Git Bash）执行 bash 命令。

主要用途：运行 skill 里的脚本 + skill 内环境配置（装依赖/建虚拟环境等）。
三道护栏按序执行：① 危险命令拦截（正则黑名单）② cwd 白名单（resolve 防逃逸）
③ 超时 + 输出截断。护栏拦截返回具体文案供 LLM 调整，真异常由 factory 层降级。
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BashConfig:
    """run_bash 工具配置（graph.py 从 BotConfig 组装，经闭包绑定进工具）。"""
    enabled: bool = True
    shell: str = "bash"
    timeout: int = 30
    max_output: int = 4000
    allowed_roots: list[str] = field(default_factory=list)
    project_root: Path = Path(".")


# (label, pattern)——防御性深水，非完整保证；真正信任边界是 skill 内容与 LLM 行为。
DANGEROUS_PATTERNS: list[tuple[str, str]] = [
    ("删除根目录", r"\brm\s+-(?:rf|fr)\s+/(?:\s|;|$)"),
    ("删除家目录", r"\brm\s+-(?:rf|fr)\s+~(?:\s|;|$)"),
    ("删除根下全部", r"\brm\s+-(?:rf|fr)\s+/\*"),
    ("递归改根权限", r"\bchmod\s+-R\s+777\s+/"),
    ("磁盘写", r"\bdd\s+if=/dev/"),
    ("格式化磁盘", r"\bmkfs\b"),
    ("关机重启", r"\b(shutdown|reboot|poweroff|halt)\b"),
    ("删除 .env", r"\brm\s+(?:-[a-z]*\s+)?\.env\b"),
    ("截断 .env", r">+\s*\.env\b"),
    ("管道到 shell", r"\b(curl|wget)\b.*\|\s*(?:sudo\s+)?(?:ba|z)?sh\b"),
]


def _is_blocked(command: str) -> str | None:
    """命令命中危险模式 → 返回模式标签；否则 None。"""
    for label, pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, command):
            return label
    return None


def _allowed_roots(cfg: BashConfig) -> list[Path]:
    roots = [cfg.project_root.resolve()]
    roots += [Path(r).resolve() for r in cfg.allowed_roots]
    return roots


def _resolve_cwd(cwd: str, cfg: BashConfig) -> Path:
    """空 cwd → project_root；相对 → project_root/cwd；绝对 → 原样。resolve() 展平 .. 与软链。"""
    if not cwd.strip():
        return cfg.project_root.resolve()
    path = Path(cwd.strip())
    if not path.is_absolute():
        path = cfg.project_root / path
    return path.resolve()


def _within_roots(path: Path, roots: list[Path]) -> bool:
    return any(path == root or path.is_relative_to(root) for root in roots)


async def run_bash(command: str, cwd: str = "", *, cfg: BashConfig) -> str:
    """执行 bash 命令返回「退出码 + 输出」文本；护栏拦截返回具体文案。"""
    blocked = _is_blocked(command)
    if blocked:
        return f"已拦截：命令命中危险模式 {blocked}，不予执行。"

    path = _resolve_cwd(cwd, cfg)
    roots = _allowed_roots(cfg)
    if not _within_roots(path, roots):
        shown = ", ".join(str(r) for r in roots)
        return f"工作目录 {path} 不在允许的根目录内。允许：{shown}"

    # Task 2 会实现真正执行；此刻先占位返回（测试只断言前两道闸）
    return "（执行主体见 Task 2）"
