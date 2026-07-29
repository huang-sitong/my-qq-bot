import asyncio
import json
import logging

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.graph.state import CompiledStateGraph as CompiledGraph

from bot.core.memory import MemoryStore
from bot.transport.http.client import SatoriApiClient
from bot.transport.websocket.client import SatoriClient
from object.satori import ChannelType, EventBody, LoginList

logger = logging.getLogger(__name__)


class MessageHandler:
    """Orchestrates message dispatch from Satori events to the LangGraph.

    Messages are validated and enqueued in ``handle()``, then processed
    by a background worker that serializes per-thread_id processing.
    """

    def __init__(
        self,
        client: SatoriClient,
        graph: CompiledGraph,
        persona: str,
        memory_store: MemoryStore,
        extract_llm: ChatOpenAI,
        api_client: SatoriApiClient,
    ) -> None:
        self.client = client
        self.graph = graph
        self._persona = persona
        self._memory_store = memory_store
        self._extract_llm = extract_llm
        self._api_client = api_client
        self._bot_id: str | None = None
        self._bot_name: str | None = None
        self._queue: asyncio.Queue[dict | None] = asyncio.Queue()
        self._locks: dict[str, asyncio.Lock] = {}
        self._worker_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the background message worker."""
        self._worker_task = asyncio.create_task(self._worker())
        logger.info("Message worker started")

    async def stop(self) -> None:
        """Signal the worker to stop and wait for pending messages."""
        await self._queue.put(None)  # Sentinel
        if self._worker_task is not None:
            await self._worker_task
            self._worker_task = None
        logger.info("Message worker stopped")

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
            self.client.config.api_user_id = self._bot_id
            logger.info("Bot info set: id=%s name=%s", self._bot_id, self._bot_name)

    async def handle(self, event: EventBody) -> None:
        """Validate and enqueue an incoming message event."""
        if event.message is None or event.message.content is None:
            return

        # 1) Build identifiers
        platform = event.platform or "unknown"
        channel_id = event.channel.id if event.channel else ""
        user_id = event.user.id if event.user else ""
        thread_id = f"{platform}:{channel_id}"

        # 2) Strip @-mention prefix and fast-reject empty content
        content = self._strip_mention(event.message.content)
        if not content.strip():
            return

        # 3) Enqueue for background processing
        await self._queue.put({
            "event": event,
            "platform": platform,
            "channel_id": channel_id,
            "user_id": user_id,
            "thread_id": thread_id,
            "content": content,
        })

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------

    async def _worker(self) -> None:
        """Background worker: dequeue and process messages sequentially.

        Per-thread_id locks serialise same-conversation messages to
        prevent LangGraph checkpoint conflicts.
        """
        while True:
            item = await self._queue.get()
            if item is None:  # Sentinel — shutdown
                self._queue.task_done()
                break
            thread_id: str = item["thread_id"]
            lock = self._locks.setdefault(thread_id, asyncio.Lock())
            async with lock:
                try:
                    await self._process(item)
                except Exception:
                    logger.exception("Message processing failed for thread %s", thread_id)
            self._queue.task_done()

    async def _process(self, item: dict) -> None:
        """Process a single message through routing → graph → reply → memory."""
        event: EventBody = item["event"]
        platform: str = item["platform"]
        channel_id: str = item["channel_id"]
        user_id: str = item["user_id"]
        thread_id: str = item["thread_id"]
        content: str = item["content"]

        # 1) Routing: determine if the bot should respond
        if event.channel and event.channel.type == ChannelType.DIRECT:
            should_respond = True       # Private chat: always respond
        elif self._is_mentioned(event.message.content):
            should_respond = True       # Group chat: @-mentioned
        else:
            should_respond = False      # Group chat: let graph's router decide

        # 2) Build session_id (for logging)
        session_id = f"{platform}:{channel_id}:{user_id}"

        # 3) Load user memories
        memories_text = self._memory_store.format_memories(user_id)

        # 4) Build message with user identity for group chat
        is_group = event.channel and event.channel.type != ChannelType.DIRECT
        if is_group:
            user_name = event.user.nick or event.user.name or event.user.id
            new_message = HumanMessage(content=content, name=user_name)
        else:
            new_message = HumanMessage(content=content)

        # 5) Invoke graph (router node handles the should_respond decision)
        logger.info(
            "Processing message from %s (session=%s, thread=%s): %.60s",
            user_id, session_id, thread_id, content,
        )
        try:
            result = await self.graph.ainvoke(
                {
                    "new_message": new_message,
                    "session_id": session_id,
                    "guild_id": event.guild.id if event.guild else "",
                    "channel_id": channel_id,
                    "persona": self._persona,
                    "user_memories": memories_text,
                    "reply_text": "",
                    "should_respond": should_respond,
                    "bot_name": self._bot_name or "",
                },
                {"configurable": {"thread_id": thread_id}},
            )
        except Exception:
            logger.exception("Graph invoke failed for session %s", session_id)
            return

        # 6) Send reply via HTTP API
        reply_text = result.get("reply_text", "")
        if reply_text:
            await self._send_reply(channel_id, reply_text)

        # 7) Extract long-term memories from this exchange
        if reply_text:
            await self._extract_memories(user_id, content, reply_text)

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
            return after_tag.lstrip()
        return content

    # ------------------------------------------------------------------
    # Reply sending
    # ------------------------------------------------------------------

    async def _send_reply(self, channel_id: str, content: str) -> None:
        """Send reply text to the source channel via Satori HTTP API."""
        try:
            await self._api_client.send_message(channel_id, content)
        except Exception:
            logger.exception("Failed to send reply to channel %s", channel_id)

    # ------------------------------------------------------------------
    # Memory extraction
    # ------------------------------------------------------------------

    async def _extract_memories(self, user_id: str, user_message: str, bot_reply: str) -> None:
        """Extract user facts from conversation and persist them."""
        prompt = MemoryStore.EXTRACT_PROMPT.format(
            user_message=user_message, bot_reply=bot_reply,
        )
        try:
            response = await self._extract_llm.ainvoke(prompt)
            memories = MemoryStore.parse_extraction(response.content)
            if memories:
                self._memory_store.store_memories(user_id, memories)
                logger.info("Stored %d memories for user %s", len(memories), user_id)
        except Exception:
            logger.debug("Memory extraction skipped (non-critical)", exc_info=True)
