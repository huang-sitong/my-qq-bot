import logging
import time

from langchain_core.messages import HumanMessage
from langgraph.graph.state import CompiledStateGraph as CompiledGraph

from bot.client import SatoriClient
from data_object.satori import EventBody, LoginList

logger = logging.getLogger(__name__)


class MessageHandler:
    """Orchestrates message dispatch from Satori events to the LangGraph.

    Usage::

        handler = MessageHandler(client, graph, persona)
        client.on("message-created")(handler.handle)
        client.on("login")(handler.handle_login)
    """

    def __init__(self, client: SatoriClient, graph: CompiledGraph, persona: str) -> None:
        self.client = client
        self.graph = graph
        self._persona = persona
        self._bot_id: str | None = None
        self._bot_name: str | None = None
        self._cooldowns: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def handle_login(self, login_list: LoginList) -> None:
        """Extract bot user id and name from the login event."""
        logins = login_list.logins
        if not logins:
            return
        user = logins[0].user
        if user is not None:
            self._bot_id = user.id
            self._bot_name = user.name or user.nick or user.id
            # Set user ID on client config so HTTP API calls include Satori-User-ID
            self.client.config.api_user_id = self._bot_id
            logger.info("Bot info set: id=%s name=%s", self._bot_id, self._bot_name)

    async def handle(self, event: EventBody) -> None:
        """Process an incoming message event."""
        if event.message is None or event.message.content is None:
            return

        # 1) @-mention check
        if not self._is_mentioned(event.message.content):
            return

        # 2) Build session_id
        platform = event.platform or "unknown"
        guild_id = event.guild.id if event.guild else ""
        channel_id = event.channel.id if event.channel else ""
        user_id = event.user.id if event.user else ""
        session_id = f"{platform}:{guild_id}:{channel_id}:{user_id}"

        # 3) Cooldown
        if self._on_cooldown(session_id):
            logger.debug("Cooldown active for %s", session_id)
            return

        # 4) Strip @-mention prefix
        content = self._strip_mention(event.message.content)

        if not content.strip():
            return

        # 5) Invoke graph
        logger.info(
            "Processing message from %s (session=%s): %.60s",
            user_id, session_id, content,
        )
        try:
            await self.graph.ainvoke(
                {
                    "new_message": HumanMessage(content=content),
                    "session_id": session_id,
                    "guild_id": guild_id,
                    "channel_id": channel_id,
                    "persona": self._persona,
                    "reply_text": "",
                },
                {"configurable": {"thread_id": session_id}},
            )
        except Exception:
            logger.exception("Graph invoke failed for session %s", session_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _is_mentioned(self, content: str) -> bool:
        """Check if the message contains an @-mention of the bot.

        LLOneBot/Satori uses ``<at id="123" name="nick"/>`` XML tags for mentions.
        """
        if self._bot_id is None:
            return False
        return f'<at id="{self._bot_id}"' in content

    def _strip_mention(self, content: str) -> str:
        """Remove a leading ``<at …/>`` mention tag from message content."""
        idx = content.find(">")
        if idx != -1:
            after_tag = content[idx + 1:]
            # Also strip any trailing whitespace after the tag
            return after_tag.lstrip()
        return content

    def _on_cooldown(self, session_id: str) -> bool:
        """Enforce a 3-second minimum interval between messages per session."""
        now = time.time()
        last = self._cooldowns.get(session_id, 0.0)
        if now - last < 3.0:
            return True
        self._cooldowns[session_id] = now
        return False
