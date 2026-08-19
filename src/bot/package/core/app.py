"""应用运行时容器。

``boot.create_app`` 完成全部依赖装配后返回 :class:`BotApplication`；
本类只负责生命周期（start / run / stop）与统一的资源关闭顺序。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from bot.package.pipeline.pipeline import MessagePipeline
from bot.package.platform.satori.adapter import SatoriAdapter

logger = logging.getLogger(__name__)


@dataclass
class AppDependencies:
    """create_app 装配出的依赖集合。"""

    config: Any
    platform: SatoriAdapter
    pipeline: MessagePipeline
    graph: Any
    checkpointer: Any
    command_registry: Any = None
    command_services: Any = None
    index_worker: Any = None
    rag_service: Any = None
    document_store: Any = None
    memory_store: Any = None
    vision_service: Any = None
    db_manager: Any = None


class BotApplication:
    """装配完成的 bot 运行时。"""

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
