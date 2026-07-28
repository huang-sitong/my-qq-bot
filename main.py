import asyncio
import logging
import os
import shutil
from pathlib import Path

from bot import (
    BotConfig,
    MemoryStore,
    MessageHandler,
    SatoriApiClient,
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


def _init_db_dir(db_dir: str) -> None:
    """Create db directory and migrate old root-level database files."""
    os.makedirs(db_dir, exist_ok=True)

    old_checkpoint = Path("bot_memory.sqlite")
    new_checkpoint = Path(db_dir) / "checkpoint.sqlite"
    if old_checkpoint.exists() and not new_checkpoint.exists():
        shutil.copy2(old_checkpoint, new_checkpoint)
        logger.info("Migrated %s → %s", old_checkpoint, new_checkpoint)
        for suffix in ("-shm", "-wal"):
            old_file = Path(f"bot_memory.sqlite{suffix}")
            if old_file.exists():
                shutil.copy2(old_file, Path(db_dir) / f"checkpoint.sqlite{suffix}")


async def main():
    logger.info("Starting QQ bot ...")

    # --- Initialise components ---
    config = BotConfig()

    # Ensure db directory exists and migrate old database files
    _init_db_dir(config.db_dir)

    client = SatoriClient(config)
    api_client = SatoriApiClient(config)

    persona = load_persona(config.persona_prompt)
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
