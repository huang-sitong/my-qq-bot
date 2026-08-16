"""命令应用服务容器。

``CommandServices`` 是命令用例所需的依赖集合，属于应用服务层；它不应该放在
``domain`` 中。保留 ``domain.bot.command.CommandServices`` 仅为兼容旧导入路径。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_openai import ChatOpenAI
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from langgraph.graph.state import CompiledStateGraph

    from bot.core.compaction import ContextCompactor
    from knowledge.service import RagService
    from memory import MemoryStore
    from skill import SkillRegistry
    from vision.service import VisionService


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
    compactor: ContextCompactor | None = None
    mcp_tool_names: tuple[str, ...] = ()
    mcp_tool_count: int = 0
    metrics_provider: Callable[[], dict] | None = None
