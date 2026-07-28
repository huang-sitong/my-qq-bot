import asyncio
import logging
import os

from bot import (
    MemoryStore,
    MessageHandler,
    SatoriApiClient,
    SatoriClient,
    create_graph,
    setup_llm,
)
from common import (
    BotConfig,
    DEFAULT_PERSONA_PROMPT,
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
    graph, checkpointer = await create_graph(llm, db_dir=config.db_dir)

    memory_store = MemoryStore(db_dir=config.db_dir)
    handler = MessageHandler(client, graph, persona, memory_store, llm, api_client)

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
        await api_client.close()
        logger.info("Bye.")


if __name__ == "__main__":
    asyncio.run(main())
