import asyncio
import logging

from bot import BotConfig, SatoriClient
from data_object.satori import EventBody

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("bot")


async def main():
    config = BotConfig()
    client = SatoriClient(config)

    @client.on("message-created")
    async def on_message(event: EventBody):
        content = event.message.content if event.message else "(no content)"
        name = event.user.name if event.user else "unknown"
        logger.info("Message from %s: %s", name, content)

    @client.on("login")
    async def on_login(login_list):
        for login in login_list.logins:
            logger.info("Login: %s on %s (status=%s)", login.user.name if login.user else "?", login.platform, login.status)

    try:
        await client.run()
    except KeyboardInterrupt:
        logger.info("Shutting down …")
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
