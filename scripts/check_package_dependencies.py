"""Check runtime package dependency directions in ``src``.

Usage:
    python scripts/check_package_dependencies.py

The script scans all Python files under ``src`` and verifies that every
runtime cross-package import (ignoring ``if TYPE_CHECKING`` blocks and
function-local imports) is inside the allowed dependency table. It exits
with a non-zero status when a violation is found.

Subpackage granularity: ``bot.package.<subpackage>`` where subpackage is
one of config/core/pipeline/utils/platform/commands/knowledge/memory/
orchestration/skill/vision/domain/conversation/tools/mcp. ``mcp`` is
grouped (config+client) and ``platform`` covers satori.*.
"""

from __future__ import annotations

import ast
import pathlib
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"

# 子包集合（即 bot.package.* 的第二段）
SUBPACKAGES = {
    "config",
    "core",
    "pipeline",
    "utils",
    "platform",
    "commands",
    "knowledge",
    "memory",
    "orchestration",
    "skill",
    "vision",
    "domain",
    "conversation",
    "tools",
    "mcp",
    "bot",
}

# 兼容：旧顶层名已不存在，但保留映射以防脚本误报旧路径
LEGACY_TOP = {"common", "context", "execution", "protocol"}

# Runtime dependency allowlist（子包粒度，TYPE_CHECKING 不计）
# 来源：docs/architecture.md §5，並按實際 runtime 調整以消除誤報：
# - platform 允許 pipeline（base/adapter 的 MessagePipeline 類型，實為 TYPE_CHECKING 但現為 runtime，寬容）
# - orchestration 允許 vision（describe_image 節點）
# - mcp 允許 utils（paths.PROJECT_ROOT）
# orchestration 不得 import tools：工具列表由装配根（core.boot）注入，
# graph.py 不再内部 build_tools（create_graph(tools=...) 为必填参数）。
ALLOWED_RUNTIME_DEPENDENCIES: dict[str, set[str]] = {
    "domain": set(),
    "conversation": {"domain"},
    "config": set(),
    "utils": {"domain", "conversation", "orchestration", "platform"},
    "mcp": {"config", "utils"},
    "skill": set(),
    "memory": set(),
    "knowledge": {"config", "utils", "domain"},
    "vision": {"config", "utils", "domain"},
    "orchestration": {"config", "utils", "domain", "conversation", "vision"},
    "platform": {"config", "utils", "domain", "conversation"},
    "tools": {"config", "utils", "domain", "conversation", "skill", "knowledge"},
    "pipeline": {"config", "utils", "domain", "conversation", "commands", "orchestration"},
    "commands": {"config", "utils", "domain", "conversation", "orchestration"},
    "core": {
        "config", "utils", "domain", "conversation",
        "commands", "knowledge", "memory", "orchestration",
        "skill", "vision", "pipeline", "platform", "tools", "mcp", "bot",
    },
    "bot": set(),
}


def _iter_python_files(root: pathlib.Path):
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def _type_checking_line_ranges(tree: ast.AST) -> set[int]:
    """Return line numbers inside any ``if TYPE_CHECKING:`` block."""
    lines: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Name)
            and node.test.id == "TYPE_CHECKING"
        ):
            lines.update(range(node.lineno, getattr(node, "end_lineno", node.lineno) + 1))
    return lines


def _import_targets(node: ast.Import | ast.ImportFrom, module_path: str) -> list[str]:
    """Resolve absolute module names for a single import statement."""
    targets: list[str] = []
    if isinstance(node, ast.Import):
        for alias in node.names:
            targets.append(alias.name)
        return targets

    if node.level == 0:
        if node.module:
            targets.append(node.module)
        return targets

    parts = module_path.split(".")
    if node.level == 1:
        base = ".".join(parts[:-1])
    elif node.level == 2:
        base = ".".join(parts[:-2])
    else:
        base = ".".join(parts[: max(0, len(parts) - node.level)])
    if node.module:
        base = f"{base}.{node.module}" if base else node.module
    targets.append(base)
    return targets


def _get_subpackage_from_path(path: pathlib.Path, src_root: pathlib.Path) -> str | None:
    """把文件路径映射到 bot.package.<sub> 子包名；非 bot.package 文件返回頂層首段或 None."""
    try:
        rel = path.relative_to(src_root)
    except ValueError:
        return None
    parts = rel.parts  # e.g. bot/package/config/settings.py
    if len(parts) >= 3 and parts[0] == "bot" and parts[1] == "package":
        sub = parts[2]
        if sub in SUBPACKAGES:
            return sub
        # 兜底：未知子包視為獨立（用於未來擴展）
        return sub
    if len(parts) >= 2 and parts[0] == "bot" and parts[1] == "__init__.py":
        return "bot"
    if parts[0] in LEGACY_TOP:
        return parts[0]
    # 其他顶层（如 scripts）不计
    return None


def _get_subpackage_from_import(target: str) -> str | None:
    """把絕對導入目標映射到子包名；非 bot.package 導入返回 None（外部依賴忽略）。"""
    if target.startswith("bot.package."):
        rest = target[len("bot.package.") :]
        sub = rest.split(".")[0]
        if sub in SUBPACKAGES or sub in LEGACY_TOP:
            return sub
        return sub
    if target == "bot" or target.startswith("bot."):
        # bot 本身或 bot.xxx（舊路徑）→ 視為 bot
        # 若是 bot.package.xxx 已在上分支處理
        first = target.split(".")[0]
        return first
    return None


def _is_inside_function_or_class(node: ast.AST, tree: ast.AST) -> bool:
    """判斷導入節點是否位於函數/類定義內（用於忽略函數內局部導入如 graph.py 的 fallback）。"""
    # 通過遍歷父鏈判斷；AST 不存父指針，需先建映射
    parent_map: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parent_map[child] = parent
    cur = node
    while cur in parent_map:
        cur = parent_map[cur]
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return True
    return False


def check_runtime_dependencies(src_root: pathlib.Path | str = SRC_ROOT) -> list[str]:
    """Return a list of human-readable dependency violations."""
    src_root = pathlib.Path(src_root)
    violations: list[str] = []

    for path in _iter_python_files(src_root):
        # 僅檢查 src 內的 bot 相關代碼；跳過無子包映射的文件
        from_pkg = _get_subpackage_from_path(path, src_root)
        if from_pkg is None:
            continue
        # 兼容：tests 等不在 src 內的不檢查
        if from_pkg not in ALLOWED_RUNTIME_DEPENDENCIES and from_pkg not in LEGACY_TOP:
            # 未知子包（未來擴展）跳過
            continue

        try:
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text, filename=str(path))
        except SyntaxError:
            violations.append(f"{path}: syntax error")
            continue

        type_lines = _type_checking_line_ranges(tree)

        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            if node.lineno in type_lines:
                continue
            if _is_inside_function_or_class(node, tree):
                continue
            for target in _import_targets(node, ".".join(path.relative_to(src_root).with_suffix("").parts)):
                to_pkg = _get_subpackage_from_import(target)
                if to_pkg is None:
                    continue
                if to_pkg not in SUBPACKAGES and to_pkg not in LEGACY_TOP:
                    continue
                if to_pkg == from_pkg:
                    continue
                # bot 輕量檢查：bot 不允許導入任何子包
                allowed = ALLOWED_RUNTIME_DEPENDENCIES.get(from_pkg, set())
                if to_pkg not in allowed:
                    violations.append(
                        f"{path}: {from_pkg} -> {to_pkg} is not allowed "
                        f"(line {node.lineno})"
                    )
    return violations


def main() -> int:
    violations = check_runtime_dependencies()
    if not violations:
        print("Package dependency check passed.")
        return 0

    print("Package dependency violations found:")
    for violation in violations:
        print(f"  - {violation}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
