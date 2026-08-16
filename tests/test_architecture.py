"""架构改造后的包结构与兼容层测试。"""

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


def test_old_compatibility_paths_still_work():
    from bot.core.commands import CommandServices as OldCommandServices
    from bot.core.memory import MemoryStore as OldMemoryStore
    from bot.core.rag import RagService as OldRagService
    from bot.core.skills import SkillRegistry as OldSkillRegistry
    from bot.core.vision import VisionService as OldVisionService
    from bot.transport.http.client import SatoriApiClient as OldApiClient
    from bot.transport.websocket.client import SatoriClient as OldClient
    from domain.bot.message import IncomingMessage as OldIncomingMessage
    from domain.bot.router import RouteDecision as OldRouteDecision

    assert OldCommandServices is commands.CommandServices
    assert OldMemoryStore is memory.MemoryStore
    assert OldRagService is knowledge.RagService
    assert OldSkillRegistry is skill.SkillRegistry
    assert OldVisionService is vision.VisionService
    assert OldApiClient is protocol.SatoriApiClient
    assert OldClient is protocol.SatoriClient
    assert OldIncomingMessage is conversation.IncomingMessage
    assert OldRouteDecision is conversation.RouteDecision
