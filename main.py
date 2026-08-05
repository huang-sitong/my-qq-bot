import asyncio
import logging
import os

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
from common import (
    BotConfig,
    DEFAULT_PERSONA_PROMPT,
)
from bot.core.mcp import load_mcp_tools

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("bot")


async def main():
    logger.info("Starting QQ bot ...")

    # --- Initialise components ---
    config = BotConfig()

    # Ensure db directory exists
    os.makedirs(config.db_dir, exist_ok=True)

    client = SatoriClient(config)
    api_client = SatoriApiClient(config)

    persona = config.persona_prompt.strip() or DEFAULT_PERSONA_PROMPT
    logger.info("Persona: %.80s", persona)

    llm = setup_llm(
        model=config.llm_model,
        temperature=config.llm_temperature,
        max_retries=config.llm_max_retries,
        request_timeout=config.llm_request_timeout,
    )
    rag_service = RagService(config) if config.rag_enabled else None
    memory_store = MemoryStore(db_dir=config.db_dir)
    vision_service = None
    if config.vision_enabled:
        vision_service = VisionService(
            base_url=config.ollama_base_url,
            model=config.vision_model,
            timeout=config.vision_timeout,
            max_images=config.vision_max_images,
        )
    mcp_tools = []
    if config.mcp_enabled:
        mcp_tools = await load_mcp_tools(config.mcp_server_connections())
        logger.info("Loaded %d MCP tools", len(mcp_tools))
    graph, checkpointer = await create_graph(
        llm, config, db_dir=config.db_dir, rag_service=rag_service, memory_store=memory_store,
        vision_service=vision_service, mcp_tools=mcp_tools,
    )

    handler = MessageHandler(
        client, graph, persona, api_client,
        bot_config=config,
    )

    # --- Register event handlers ---
    client.on("message-created")(handler.handle)
    client.on("login")(handler.handle_login)

    # --- Start message worker ---
    await handler.start()

    # --- Run ---
    try:
        await client.run()
    except KeyboardInterrupt:
        logger.info("Shutting down ...")
    finally:
        await handler.stop()
        await client.disconnect()
        await api_client.close()
        if rag_service is not None:
            rag_service.close()
        if vision_service is not None:
            await vision_service.close()
        memory_store.close()
        logger.info("Bye.")


if __name__ == "__main__":
    asyncio.run(main())
