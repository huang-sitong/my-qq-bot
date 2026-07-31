import asyncio
import logging

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.graph.state import CompiledStateGraph as CompiledGraph

from bot.core.memory import MemoryStore
from bot.transport.http.client import SatoriApiClient
from bot.transport.websocket.client import SatoriClient
from object.satori import EventBody, LoginList

logger = logging.getLogger(__name__)


def _strip_leading_mention(content: str) -> str:
    """Remove a leading ``<at …/>`` mention tag so stored content is clean."""
    idx = content.find(">")
    return content[idx + 1:].lstrip() if idx != -1 else content


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
        rag_service=None,
    ) -> None:
        self.client = client
        self.graph = graph
        self._persona = persona
        self._memory_store = memory_store
        self._extract_llm = extract_llm
        self._api_client = api_client
        self._rag_service = rag_service
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

        # 1) Fast-reject entirely empty messages
        if not event.message.content.strip():
            return

        # 2) Build identifiers
        platform = event.platform or "unknown"
        guild_id = event.guild.id if event.guild else ""
        channel_id = event.channel.id if event.channel else ""
        user_id = event.user.id if event.user else ""
        thread_id = f"{platform}:{guild_id}:{channel_id}"

        # 3) Enqueue for background processing
        await self._queue.put({
            "event": event,
            "platform": platform,
            "guild_id": guild_id,
            "channel_id": channel_id,
            "user_id": user_id,
            "thread_id": thread_id,
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
        """Process a single message: extract data → graph → reply → memory."""
        event: EventBody = item["event"]
        platform: str = item["platform"]
        guild_id: str = item["guild_id"]
        channel_id: str = item["channel_id"]
        user_id: str = item["user_id"]
        thread_id: str = item["thread_id"]

        # --- Mechanical data extraction (no business logic) ---
        channel_type = event.channel.type if event.channel else 0
        raw_content = event.message.content or ""
        user_name = ""
        if event.user:
            user_name = event.user.nick or event.user.name or event.user.id or ""
        session_id = f"{platform}:{guild_id}:{channel_id}:{user_id}"
        memories_text = self._memory_store.format_memories(user_id)

        # --- Invoke graph ---
        logger.info(
            "Processing message from %s (session=%s, thread=%s): %.60s",
            user_id, session_id, thread_id, raw_content,
        )
        try:
            result = await self.graph.ainvoke(
                {
                    "new_message": HumanMessage(content=""),  # placeholder
                    "session_id": session_id,
                    "thread_id": thread_id,
                    "persona": self._persona,
                    "user_memories": memories_text,
                    "reply_text": "",
                    "should_respond": False,  # detect_intent decides
                    "bot_name": self._bot_name or "",
                    "bot_id": self._bot_id or "",
                    "channel_type": channel_type,
                    "raw_content": raw_content,
                    "user_name": user_name,
                },
                {"configurable": {"thread_id": thread_id}},
            )
        except Exception:
            logger.exception("Graph invoke failed for session %s", session_id)
            return

        # --- Post-graph: reply + memory extraction + RAG indexing ---
        reply_text = result.get("reply_text", "")
        if reply_text:
            await self._send_reply(channel_id, reply_text)
            await self._extract_memories(user_id, raw_content, reply_text)
            if self._rag_service is not None:
                await self._index_turn(thread_id, user_id, user_name, raw_content, reply_text)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # RAG indexing
    # ------------------------------------------------------------------

    async def _index_turn(
        self,
        thread_id: str,
        user_id: str,
        user_name: str,
        user_message: str,
        bot_reply: str,
    ) -> None:
        """将本轮对话（用户消息 + Bot 回复）索引入向量库。失败仅降级。"""
        content = _strip_leading_mention(user_message)
        await self._rag_service.index_turn(
            thread_id, user_id, user_name, content, bot_reply,
        )
