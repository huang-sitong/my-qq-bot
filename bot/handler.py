import asyncio
import logging

from langgraph.graph.state import CompiledStateGraph as CompiledGraph

from bot.core.commands import (
    CommandActor,
    CommandContext,
    CommandRegistry,
    CommandServices,
    parse_command,
    run_command,
)
from bot.core.utils import parse_content
from bot.transport.http.client import SatoriApiClient
from bot.transport.websocket.client import SatoriClient
from object.satori import EventBody, LoginList

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
        api_client: SatoriApiClient,
        bot_config=None,
        command_registry: CommandRegistry | None = None,
        command_services: CommandServices | None = None,
    ) -> None:
        self.client = client
        self.graph = graph
        self._persona = persona
        self._api_client = api_client
        self._bot_config = bot_config
        self._command_registry = command_registry
        self._command_services = command_services
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
            self._api_client.set_user_id(self._bot_id)
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
        channel_id: str = item["channel_id"]
        user_id: str = item["user_id"]
        thread_id: str = item["thread_id"]

        # --- Mechanical data extraction (no business logic) ---
        # 强制 int：ChannelType 是 IntEnum，若原样入 checkpoint 会触发
        # langgraph 未注册类型反序列化警告（未来版本会升级为报错）
        channel_type = int(event.channel.type) if event.channel else 0
        raw_content = event.message.content or ""
        user_name = ""
        if event.user:
            user_name = event.user.nick or event.user.name or event.user.id or ""

        # --- Message classification (ingress) ---
        parsed = parse_content(raw_content)
        content_kind = parsed.kind.value
        image_srcs = [a.src for a in parsed.attachments if a.type == "img"]

        if (
            self._command_registry is not None
            and self._command_services is not None
            and self._bot_config is not None
            and self._bot_config.command_enabled
            and content_kind == "text"
        ):
            parsed_cmd = parse_command(
                parsed.clean_text, self._bot_config.command_prefix
            )
            if parsed_cmd is not None:
                command = self._command_registry.resolve(parsed_cmd.name)
                if command is not None:
                    actor = CommandActor(
                        user_id=user_id,
                        name=user_name,
                        is_admin=user_id in (self._bot_config.admin_ids or []),
                    )
                    ctx = CommandContext(
                        raw=raw_content,
                        actor=actor,
                        platform=item["platform"],
                        guild_id=item["guild_id"],
                        channel_id=channel_id,
                        thread_id=thread_id,
                        channel_type=channel_type,
                        args=parsed_cmd.args,
                        config=self._bot_config,
                        services=self._command_services,
                    )
                    if parsed_cmd.error:
                        reply_text = f"指令参数错误，用法：{command.usage}"
                    else:
                        reply_text = (await run_command(command, ctx)).text
                    if reply_text:
                        await self._send_reply(channel_id, reply_text)
                    return

        # --- Invoke graph ---
        logger.info(
            "Processing %s message from %s (thread=%s): %.60s",
            content_kind, user_id, thread_id, raw_content,
        )
        # recursion_limit 是 LangGraph 的兜底，真实上限由 rag_max_agent_rounds 决定
        # （4 + 2n 个 super-step，n = 工具轮次），这里按配置放一个充裕的安全网。
        # 与 call_llm_node 读取同一份配置（BOT_RAG_MAX_AGENT_ROUNDS），
        # 避免 memory-only 模式下配置大于 3 时 recursion_limit 被低估触发 GraphRecursionError。
        max_rounds = (
            self._bot_config.rag_max_agent_rounds
            if self._bot_config is not None
            else 3
        )
        recursion_limit = 2 * max_rounds + 8
        try:
            result = await self.graph.ainvoke(
                {
                    "thread_id": thread_id,
                    "persona": self._persona,
                    "reply_text": "",
                    "should_respond": False,  # detect_intent decides
                    "bot_name": self._bot_name or "",
                    "bot_id": self._bot_id or "",
                    "tool_rounds": 0,
                    "user_id": user_id,
                    "channel_type": channel_type,
                    "user_name": user_name,
                    "content_kind": content_kind,
                    "llm_text": parsed.llm_text,
                    "clean_text": parsed.clean_text,
                    "mentions": parsed.mentions,
                    "image_srcs": image_srcs,
                },
                {
                    "configurable": {"thread_id": thread_id},
                    "recursion_limit": recursion_limit,
                },
            )
        except Exception:
            logger.exception("Graph invoke failed for thread %s", thread_id)
            return

        # --- Post-graph: reply (RAG indexing is now a graph node after summarize) ---
        reply_text = result.get("reply_text", "")
        if reply_text:
            await self._send_reply(channel_id, reply_text)

    # ------------------------------------------------------------------
    # Reply sending
    # ------------------------------------------------------------------

    async def _send_reply(self, channel_id: str, content: str) -> None:
        """Send reply text to the source channel via Satori HTTP API."""
        try:
            await self._api_client.send_message(channel_id, content)
        except Exception:
            logger.exception("Failed to send reply to channel %s", channel_id)
