"""应用装配入口。

把原先散落在 ``main.py`` 的组件初始化、降级策略和依赖连接集中到
:func:`create_app`，供薄入口直接调用。
"""

from __future__ import annotations

import logging
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from dotenv import dotenv_values, find_dotenv

from bot.package.commands import CommandServices, build_command_registry
from bot.package.config import BotConfig
from bot.package.config.settings import DEFAULT_PERSONA_PROMPT
from bot.package.conversation.events import ConversationTurnCompleted
from bot.package.conversation.identity import BotIdentity
from bot.package.core.app import AppDependencies, BotApplication
from bot.package.core.database import DatabaseManager
from bot.package.core.llm import setup_llm
from bot.package.knowledge import create_document_store, create_rag_service
from bot.package.knowledge.index_worker import IndexWorker
from bot.package.knowledge.turn_index_projection import TurnIndexProjection
from bot.package.mcp import create_mcp_tools
from bot.package.memory import create_memory_store
from bot.package.orchestration.compaction import ContextCompactor
from bot.package.orchestration.conversation_repository import LangGraphConversationRepository
from bot.package.orchestration.graph import create_graph
from bot.package.pipeline.dispatcher import MessageDispatcher
from bot.package.pipeline.pipeline import MessagePipeline
from bot.package.platform.satori.adapter import SatoriAdapter
from bot.package.platform.satori.http import SatoriApiClient
from bot.package.platform.satori.websocket import SatoriClient
from bot.package.skill import create_skill_registry
from bot.package.tools import build_tools
from bot.package.tools.domain import BashConfig
from bot.package.utils.event_bus import InMemoryDomainEventBus
from bot.package.utils.logging import setup_logging
from bot.package.utils.paths import PROJECT_ROOT
from bot.package.vision import create_vision_service

logger = logging.getLogger("bot")


def _bot_version() -> str:
    try:
        return version("qq-bot")
    except PackageNotFoundError:
        return "0.1.0"


def load_env_values() -> dict[str, str]:
    """读取 .env 文件内容，作为 MCP 配置 ``${VAR}`` 插值源。"""
    env_file = find_dotenv()
    return dotenv_values(env_file) if env_file else {}


def load_config() -> BotConfig:
    return BotConfig()


async def create_app(config: BotConfig | None = None) -> BotApplication:
    """装配并返回可运行的 :class:`BotApplication`。

    初始化失败的可选组件沿用项目约定：RAG / DocumentStore / Vision / MCP /
    Skill 失败只降级，不阻断启动。
    """
    setup_logging("log")
    logger.info("Starting QQ bot ...")
    started_at = time.time()

    config = config or load_config()
    env_vars = load_env_values()

    db_manager = DatabaseManager(config.db_dir)
    db_manager.ensure_ready()

    client = SatoriClient(config)
    api_client = SatoriApiClient(config)
    identity = BotIdentity()

    persona = config.persona_prompt.strip() or DEFAULT_PERSONA_PROMPT
    logger.info("Persona: %.80s", persona)

    llm = setup_llm(config)

    # 各上下文工厂统一创建，内部处理 enabled 开关与异常降级
    rag_service = create_rag_service(config)
    document_store = create_document_store(config, embedder=rag_service.embedder if rag_service else None)

    memory_store = create_memory_store(config)

    vision_service = create_vision_service(config)

    mcp_tools = await create_mcp_tools(config, env_vars)

    skill_registry = create_skill_registry(config)

    bash_config = BashConfig(
        enabled=config.bash_enabled,
        shell=config.bash_shell,
        timeout=config.bash_timeout,
        max_output=config.bash_max_output,
        allowed_roots=config.bash_allowed_roots,
        project_root=PROJECT_ROOT,
    )
    send_roots = [PROJECT_ROOT] + [
        Path(root).resolve() for root in config.bash_allowed_roots
    ]
    tools = build_tools(
        rag_service=rag_service,
        document_store=document_store,
        memory_store=memory_store,
        mcp_tools=mcp_tools,
        skill_registry=skill_registry,
        bash_config=bash_config,
        file_sender=api_client,
        send_roots=send_roots,
    )
    graph, checkpointer = await create_graph(
        llm,
        config,
        db_dir=config.db_dir,
        tools=tools,
        rag_service=rag_service,
        document_store=document_store,
        memory_store=memory_store,
        vision_service=vision_service,
        mcp_tools=mcp_tools,
        skill_registry=skill_registry,
        file_sender=api_client,
    )

    compactor = None
    if graph is not None and llm is not None:
        compactor = ContextCompactor(graph, llm, config, skill_registry=skill_registry)

    conversation_repository = (
        LangGraphConversationRepository(graph) if graph is not None else None
    )
    event_bus = InMemoryDomainEventBus()
    index_worker = IndexWorker(rag_service) if rag_service is not None else None
    if index_worker is not None:
        event_bus.subscribe(
            ConversationTurnCompleted,
            TurnIndexProjection(index_worker).on_turn_completed,
        )

    command_services = CommandServices(
        version=_bot_version(),
        started_at=started_at,
        bot_name="",
        llm=llm,
        graph=graph,
        checkpointer=checkpointer,
        skill_registry=skill_registry,
        rag_service=rag_service,
        vision_service=vision_service,
        memory_store=memory_store,
        conversation_repository=conversation_repository,
        compactor=compactor,
        mcp_tool_names=tuple(tool.name for tool in mcp_tools),
        mcp_tool_count=len(mcp_tools),
    )
    command_registry = (
        build_command_registry(command_services, config.command_prefix)
        if config.command_enabled
        else None
    )

    dispatcher = MessageDispatcher(
        graph=graph,
        persona=persona,
        api_client=api_client,
        bot_config=config,
        command_registry=command_registry,
        command_services=command_services,
        compactor=compactor,
        identity=identity,
        conversation_repository=conversation_repository,
        event_bus=event_bus,
    )
    pipeline = MessagePipeline(
        dispatcher,
        bot_config=config,
        command_registry=command_registry,
        identity=identity,
        worker_count=config.message_worker_count,
        queue_maxsize=config.message_queue_maxsize,
        batch_max=config.message_batch_max,
        dedup_size=config.message_dedup_size,
    )
    platform = SatoriAdapter(
        client,
        api_client,
        pipeline=pipeline,
        command_services=command_services,
        identity=identity,
    )

    return BotApplication(
        AppDependencies(
            config=config,
            platform=platform,
            pipeline=pipeline,
            graph=graph,
            checkpointer=checkpointer,
            command_registry=command_registry,
            command_services=command_services,
            index_worker=index_worker,
            rag_service=rag_service,
            document_store=document_store,
            memory_store=memory_store,
            vision_service=vision_service,
            db_manager=db_manager,
        )
    )


__all__ = ["create_app", "load_config", "load_env_values"]
