"""应用运行时容器。

``boot.create_app`` 完成全部依赖装配后返回 :class:`BotApplication`；
本类只负责生命周期（start / run / stop）与统一的资源关闭顺序。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph.state import CompiledStateGraph

from bot.package.commands.registry import CommandRegistry
from bot.package.commands.services import CommandServices
from bot.package.config import BotConfig
from bot.package.core.database import DatabaseManager
from bot.package.knowledge.document_store import DocumentStore
from bot.package.knowledge.index_worker import IndexWorker
from bot.package.knowledge.service import RagService
from bot.package.memory import MemoryStore
from bot.package.pipeline.pipeline import MessagePipeline
from bot.package.platform.satori.adapter import SatoriAdapter
from bot.package.vision import VisionService

logger = logging.getLogger(__name__)

# 可选依赖：未启用或初始化降级时以 None 表示（boot 沿用“失败只降级、不阻断”策略）。
OptionalGraph = CompiledStateGraph | None
OptionalCheckpointer = AsyncSqliteSaver | None


@dataclass
class AppDependencies:
    """create_app 装配出的依赖集合。"""

    config: BotConfig
    platform: SatoriAdapter
    pipeline: MessagePipeline
    graph: OptionalGraph
    checkpointer: OptionalCheckpointer
    command_registry: CommandRegistry | None = None
    command_services: CommandServices | None = None
    index_worker: IndexWorker | None = None
    rag_service: RagService | None = None
    document_store: DocumentStore | None = None
    memory_store: MemoryStore | None = None
    vision_service: VisionService | None = None
    db_manager: DatabaseManager | None = None


class BotApplication:
    """装配完成的 bot 运行时。"""

    config: BotConfig
    platform: SatoriAdapter
    pipeline: MessagePipeline
    graph: OptionalGraph
    checkpointer: OptionalCheckpointer
    command_registry: CommandRegistry | None
    command_services: CommandServices | None
    index_worker: IndexWorker | None
    rag_service: RagService | None
    document_store: DocumentStore | None
    memory_store: MemoryStore | None
    vision_service: VisionService | None
    db_manager: DatabaseManager | None

    def __init__(self, deps: AppDependencies) -> None:
        self.config = deps.config
        self.platform = deps.platform
        self.pipeline = deps.pipeline
        self.graph = deps.graph
        self.checkpointer = deps.checkpointer
        self.command_registry = deps.command_registry
        self.command_services = deps.command_services
        self.index_worker = deps.index_worker
        self.rag_service = deps.rag_service
        self.document_store = deps.document_store
        self.memory_store = deps.memory_store
        self.vision_service = deps.vision_service
        self.db_manager = deps.db_manager

    async def start(self) -> None:
        """启动流水线和后台任务，并注册平台事件回调。"""
        if self.command_services is not None:
            self.command_services.metrics_provider = lambda: self.pipeline.metrics
        await self.pipeline.start()
        if self.index_worker is not None:
            await self.index_worker.start()
        self.platform.bind_pipeline(self.pipeline)
        self.platform.register_handlers()
        logger.info("Bot application started")

    async def run(self) -> None:
        """运行平台事件循环，直到收到停止信号。"""
        try:
            await self.platform.run()
        except KeyboardInterrupt:
            logger.info("Shutting down ...")

    async def stop(self) -> None:
        """按顺序关闭流水线、后台任务和外部资源。"""
        await self.pipeline.stop()
        if self.index_worker is not None:
            await self.index_worker.stop()
        await self.platform.close()
        if self.rag_service is not None:
            self.rag_service.close()
        if self.document_store is not None:
            self.document_store.close()
        if self.vision_service is not None:
            await self.vision_service.close()
        if self.memory_store is not None:
            await self.memory_store.close()
        if self.db_manager is not None:
            self.db_manager.close()
        logger.info("Bye.")


__all__ = ["AppDependencies", "BotApplication"]
