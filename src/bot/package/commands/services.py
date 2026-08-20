"""命令应用服务容器。

``CommandServices`` 是命令用例所需的依赖集合，属于应用服务层；它不应该放在
``domain`` 中。``domain`` 不再导出命令服务，统一从 ``commands`` 获取。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_openai import ChatOpenAI
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from langgraph.graph.state import CompiledStateGraph

    from bot.package.domain.repositories import ConversationRepository
    from bot.package.knowledge.service import RagService
    from bot.package.memory import MemoryStore
    from bot.package.orchestration.compaction import ContextCompactor
    from bot.package.skill import SkillRegistry
    from bot.package.vision.service import VisionService


@dataclass
class CommandServices:
    version: str
    started_at: float
    bot_name: str
    llm: ChatOpenAI | None = None
    graph: CompiledStateGraph | None = None
    checkpointer: AsyncSqliteSaver | None = None
    skill_registry: SkillRegistry | None = None
    rag_service: RagService | None = None
    vision_service: VisionService | None = None
    memory_store: MemoryStore | None = None
    conversation_repository: ConversationRepository | None = None
    compactor: ContextCompactor | None = None
    mcp_tool_names: tuple[str, ...] = ()
    mcp_tool_count: int = 0
    metrics_provider: Callable[[], dict] | None = None
