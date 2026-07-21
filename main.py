import asyncio
import logging

from bot import (
    BotConfig,
    MemoryStore,
    MessageHandler,
    SatoriClient,
    create_graph,
    load_persona,
    setup_llm,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("bot")


async def main():
    logger.info("Starting QQ bot ...")

    # --- Initialise components ---
    config = BotConfig()
    client = SatoriClient(config)

    persona = load_persona()
    logger.info("Persona: %.80s", persona)

    llm = setup_llm()
    graph = await create_graph(llm, client)

    memory_store = MemoryStore()
    handler = MessageHandler(client, graph, persona, memory_store, llm)

    # --- Register event handlers ---
    client.on("message-created")(handler.handle)
    client.on("login")(handler.handle_login)

    # --- Run ---
    try:
        await client.run()
    except KeyboardInterrupt:
        logger.info("Shutting down ...")
    finally:
        await client.disconnect()
        logger.info("Bye.")


if __name__ == "__main__":
    asyncio.run(main())
