"""Check runtime package dependency directions in ``src``.

Usage:
    python scripts/check_package_dependencies.py

The script scans all Python files under ``src`` and verifies that every
runtime cross-package import (ignoring ``if TYPE_CHECKING`` blocks) is
inside the allowed dependency table. It exits with a non-zero status when
a violation is found.
"""

from __future__ import annotations

import ast
import pathlib
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"

INTERNAL_PACKAGES = {
    "bot",
    "commands",
    "common",
    "context",
    "conversation",
    "domain",
    "execution",
    "knowledge",
    "memory",
    "orchestration",
    "protocol",
    "skill",
    "vision",
}

# Runtime dependency allowlist. Type-checking imports are intentionally not
# enforced here; they do not create module-load-time coupling.
ALLOWED_RUNTIME_DEPENDENCIES: dict[str, set[str]] = {
    "bot": {
        "commands",
        "common",
        "context",
        "conversation",
        "domain",
        "knowledge",
        "memory",
        "orchestration",
        "protocol",
        "skill",
        "vision",
    },
    "commands": {"bot", "common", "context", "domain"},
    "common": {"bot", "domain"},
    "context": {"bot", "common", "conversation", "domain"},
    "conversation": {"bot", "domain"},
    "domain": {"bot"},
    "execution": {"bot", "common", "context", "conversation", "domain", "knowledge", "skill"},
    "knowledge": {"bot", "common", "domain"},
    "memory": {"bot"},
    "orchestration": {
        "bot",
        "common",
        "context",
        "conversation",
        "domain",
        "execution",
        "knowledge",
        "vision",
    },
    "protocol": {"bot", "common", "domain"},
    "skill": {"bot"},
    "vision": {"bot", "common", "domain"},
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


def check_runtime_dependencies(src_root: pathlib.Path | str = SRC_ROOT) -> list[str]:
    """Return a list of human-readable dependency violations."""
    src_root = pathlib.Path(src_root)
    violations: list[str] = []

    for path in _iter_python_files(src_root):
        rel_path = path.relative_to(src_root)
        module_dotted = ".".join(rel_path.with_suffix("").parts)
        top_pkg = module_dotted.split(".")[0]

        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            violations.append(f"{path}: syntax error")
            continue

        type_lines = _type_checking_line_ranges(tree)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            if node.lineno in type_lines:
                continue
            for target in _import_targets(node, module_dotted):
                target_pkg = target.split(".")[0]
                if target_pkg not in INTERNAL_PACKAGES:
                    continue
                if target_pkg == top_pkg:
                    continue
                allowed = ALLOWED_RUNTIME_DEPENDENCIES.get(top_pkg, set())
                if target_pkg not in allowed:
                    violations.append(
                        f"{path}: {top_pkg} -> {target_pkg} is not allowed "
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
