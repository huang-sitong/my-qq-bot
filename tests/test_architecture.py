"""架构改造后的包结构与旧路径移除测试。"""

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

from bot.package.commands import CommandServices
from bot.package.conversation import IncomingMessage
from bot.package.knowledge import RagService
from bot.package.memory import MemoryStore
from bot.package.orchestration import ContextCompactor, create_graph
from bot.package.platform.satori import SatoriClient
from bot.package.skill import SkillRegistry
from bot.package.tools import build_tools
from bot.package.vision import VisionService


def test_new_bounded_context_packages_are_importable():
    assert SatoriClient is not None
    assert CommandServices is not None
    assert SkillRegistry is not None
    assert RagService is not None
    assert MemoryStore is not None
    assert VisionService is not None
    assert IncomingMessage is not None
    assert create_graph is not None
    assert build_tools is not None
    assert ContextCompactor is not None


def _assert_missing(module_name: str) -> None:
    with pytest.raises(ImportError):
        importlib.import_module(module_name)


OLD_TOP_LEVEL_PACKAGES = (
    "common",
    "context",
    "execution",
    "protocol",
    "commands",
    "conversation",
    "domain",
    "knowledge",
    "memory",
    "orchestration",
    "skill",
    "vision",
)

OLD_BOT_PIPELINE_PATHS = (
    "bot.core",
    "bot.core.llm",
    "bot.handler",
    "bot.core.router",
    "bot.core.dispatcher",
    "bot.core.worker",
    "bot.core.ingress",
)

OLD_COMPATIBILITY_PATHS = (
    "bot.transport",
    "bot.core.rag",
    "bot.core.skills",
    "bot.core.vision",
    "bot.core.commands",
    "bot.core.memory",
    "domain.bot",
    "bot.core.graph",
    "bot.core.nodes",
    "bot.core.tools",
    "bot.core.utils",
    "bot.core.compaction",
    "bot.core.mcp",
    "context.compaction",
)


def test_old_top_level_packages_are_removed():
    for module_name in OLD_TOP_LEVEL_PACKAGES + OLD_BOT_PIPELINE_PATHS:
        _assert_missing(module_name)


def test_old_compatibility_paths_are_removed():
    for module_name in OLD_COMPATIBILITY_PATHS:
        _assert_missing(module_name)


def test_old_top_level_source_directories_are_removed():
    repo_root = Path(__file__).resolve().parents[1]
    src_root = repo_root / "src"
    for package in OLD_TOP_LEVEL_PACKAGES:
        assert not (src_root / package).exists(), f"old package dir still exists: src/{package}"
    assert not (src_root / "bot" / "core").exists(), "old package dir still exists: src/bot/core"

    for module_name in OLD_BOT_PIPELINE_PATHS + OLD_COMPATIBILITY_PATHS:
        module_path = src_root / f"{module_name.replace('.', '/')}.py"
        assert not module_path.exists(), f"old module file still exists: src/{module_name}"


def test_package_runtime_dependencies_follow_allowlist():
    repo_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "scripts/check_package_dependencies.py"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
