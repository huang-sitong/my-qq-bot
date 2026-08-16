import asyncio
import logging
import os
import time
from importlib.metadata import PackageNotFoundError, version

from dotenv import dotenv_values, find_dotenv

from bot import (
    MemoryStore,
    MessageHandler,
    RagService,
    SatoriApiClient,
    SatoriClient,
    VisionService,
    create_graph,
    setup_llm,
)
from bot.core.compaction import ContextCompactor
from bot.core.mcp import load_mcp_tools
from commands import CommandServices, build_command_registry
from common import (
    DEFAULT_PERSONA_PROMPT,
    BotConfig,
)
from common.logging import TraceIdFilter
from common.mcp import load_mcp_servers_from_file
from knowledge.index_worker import IndexWorker
from skill import SkillRegistry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s [trace=%(trace_id)s]",
)
logging.getLogger().addFilter(TraceIdFilter())
logger = logging.getLogger("bot")


def _bot_version() -> str:
    try:
        return version("qq-bot")
    except PackageNotFoundError:
        return "0.1.0"


async def main():
    logger.info("Starting QQ bot ...")
    started_at = time.time()

    # --- Initialise components ---
    config = BotConfig()

    # .env 内容读成 dict，供 config/mcp_servers.json 的 ${ENV_VAR} 密钥插值
    # （项目约定：生产代码不直接读进程环境，dotenv_values 是纯文件读取）。
    env_file = find_dotenv()
    env_vars = dotenv_values(env_file) if env_file else {}

    # Ensure db directory exists
    os.makedirs(config.db_dir, exist_ok=True)

    client = SatoriClient(config)
    api_client = SatoriApiClient(config)

    persona = config.persona_prompt.strip() or DEFAULT_PERSONA_PROMPT
    logger.info("Persona: %.80s", persona)

    llm = setup_llm(config)
    rag_service = None
    if config.rag_enabled:
        try:
            rag_service = RagService(config)
        except Exception:
            logger.exception("RAG init failed; falling back to rag disabled")
            rag_service = None
    memory_store = MemoryStore(db_dir=config.db_dir)
    vision_service = None
    if config.vision_enabled:
        if not config.vision_base_url:
            logger.warning("vision_enabled but vision_base_url is empty; disabling vision")
        else:
            vision_service = VisionService(
                base_url=config.vision_base_url,
                model=config.vision_model,
                api_key=config.vision_api_key,
                timeout=config.vision_timeout,
                max_images=config.vision_max_images,
            )
    mcp_tools = []
    if config.mcp_enabled:
        mcp_tools = await load_mcp_tools(
            load_mcp_servers_from_file(config.mcp_servers_file, env=env_vars),
            tool_name_prefix=config.mcp_tool_name_prefix,
        )
        logger.info("Loaded %d MCP tools", len(mcp_tools))
    skill_registry = None
    if config.skills_enabled:
        skill_registry = SkillRegistry.from_directory(
            config.skills_dir, index_max=config.skills_index_max,
        )
        logger.info("Loaded %d skills from %s", skill_registry.total, config.skills_dir)
    graph, checkpointer = await create_graph(
        llm, config, db_dir=config.db_dir, rag_service=rag_service, memory_store=memory_store,
        vision_service=vision_service, mcp_tools=mcp_tools, skill_registry=skill_registry,
        file_sender=api_client,
    )
    compactor = None
    if graph is not None and llm is not None:
        compactor = ContextCompactor(
            graph, llm, config, skill_registry=skill_registry,
        )
    index_worker = None
    if rag_service is not None:
        index_worker = IndexWorker(rag_service)
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
        compactor=compactor,
        mcp_tool_names=tuple(tool.name for tool in mcp_tools),
        mcp_tool_count=len(mcp_tools),
    )
    command_registry = (
        build_command_registry(command_services, config.command_prefix)
        if config.command_enabled
        else None
    )

    handler = MessageHandler(
        client, graph, persona, api_client,
        bot_config=config,
        command_registry=command_registry,
        command_services=command_services,
        compactor=compactor,
        index_worker=index_worker,
        worker_count=config.message_worker_count,
        queue_maxsize=config.message_queue_maxsize,
        batch_max=config.message_batch_max,
        dedup_size=config.message_dedup_size,
    )
    command_services.metrics_provider = lambda: handler._worker_pool.metrics

    # --- Register event handlers ---
    client.on("message-created")(handler.handle)
    client.on("login")(handler.handle_login)

    # --- Start message worker ---
    await handler.start()
    if index_worker is not None:
        await index_worker.start()

    # --- Run ---
    try:
        await client.run()
    except KeyboardInterrupt:
        logger.info("Shutting down ...")
    finally:
        await handler.stop()
        if index_worker is not None:
            await index_worker.stop()
        await client.disconnect()
        await api_client.close()
        if rag_service is not None:
            rag_service.close()
        if vision_service is not None:
            await vision_service.close()
        await memory_store.close()
        logger.info("Bye.")


if __name__ == "__main__":
    asyncio.run(main())
