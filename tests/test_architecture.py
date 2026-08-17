"""架构改造后的包结构与兼容层移除测试。"""

import importlib

import pytest

import commands
import context
import conversation
import execution
import knowledge
import memory
import orchestration
import protocol
import skill
import vision


def test_new_bounded_context_packages_are_importable():
    assert protocol.SatoriClient is not None
    assert commands.CommandServices is not None
    assert skill.SkillRegistry is not None
    assert knowledge.RagService is not None
    assert memory.MemoryStore is not None
    assert vision.VisionService is not None
    assert conversation.IncomingMessage is not None
    assert orchestration.create_graph is not None
    assert execution.build_tools is not None
    assert context.ContextCompactor is not None


def _assert_missing(module_name: str) -> None:
    with pytest.raises(ImportError):
        importlib.import_module(module_name)


def test_old_compatibility_paths_are_removed():
    for module_name in (
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
    ):
        _assert_missing(module_name)
