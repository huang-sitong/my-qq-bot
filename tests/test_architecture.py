"""架构改造后的包结构与兼容层移除测试。"""

import pytest

import commands
import conversation
import knowledge
import memory
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


def test_old_compatibility_paths_are_removed():
    with pytest.raises(ImportError):
        import bot.transport
    with pytest.raises(ImportError):
        import bot.core.rag
    with pytest.raises(ImportError):
        import bot.core.skills
    with pytest.raises(ImportError):
        import bot.core.vision
    with pytest.raises(ImportError):
        import bot.core.commands
    with pytest.raises(ImportError):
        import bot.core.memory  # noqa: F401
    with pytest.raises(ImportError):
        import domain.bot  # noqa: F401
