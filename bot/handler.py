import asyncio
import logging
import random
import time

from langchain_core.messages import HumanMessage
from langgraph.graph.state import CompiledStateGraph as CompiledGraph

from bot.core.commands import (
    CommandContext,
    CommandRegistry,
    CommandServices,
    can_run,
    run_command,
)
from bot.core.compaction import ContextCompactor
from bot.core.rag.index_worker import IndexWorker
from bot.core.router import RouteAction, route_incoming
from bot.core.utils import IMAGE_PLACEHOLDER, MessageKind, content_to_text, parse_content
from bot.core.utils.reply_policy import should_allow_auto_reply
from bot.transport.http.client import SatoriApiClient
from bot.transport.websocket.client import SatoriClient
from object.bot.index_task import IndexTurnTask
from object.bot.message import IncomingMessage
from object.satori import ChannelType, EventBody, LoginList

logger = logging.getLogger(__name__)


class MessageHandler:
    """Orchestrates message dispatch from Satori events to the LangGraph.

    Protocol events are normalized into ``IncomingMessage``, routed outside
    the graph, and RAG indexing is enqueued to a background worker.
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
        compactor: ContextCompactor | None = None,
        index_worker: IndexWorker | None = None,
        worker_count: int = 1,
        queue_maxsize: int = 0,
    ) -> None:
        self.client = client
        self.graph = graph
        self._persona = persona
        self._api_client = api_client
        self._bot_config = bot_config
        self._command_registry = command_registry
        self._command_services = command_services
        self._compactor = compactor
        self._index_worker = index_worker
        self._bot_id: str | None = None
        self._bot_name: str | None = None
        self._worker_count = worker_count
        self._queue: asyncio.Queue[IncomingMessage | None] = asyncio.Queue(
            maxsize=queue_maxsize
        )
        self._locks: dict[str, asyncio.Lock] = {}
        self._worker_tasks: list[asyncio.Task[None]] = []
        self._last_auto_reply_at: dict[str, float] = {}
        self._random = random.Random()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the configured number of background message workers."""
        self._worker_tasks = [
            asyncio.create_task(self._worker())
            for _ in range(self._worker_count)
        ]
        logger.info("Message workers started: %d", self._worker_count)

    async def stop(self) -> None:
        """Signal workers to stop and wait for pending messages."""
        for _ in range(self._worker_count):
            await self._queue.put(None)
        if self._worker_tasks:
            await asyncio.gather(*self._worker_tasks, return_exceptions=True)
            self._worker_tasks = []
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
            if self._command_services is not None:
                self._command_services.bot_name = self._bot_name
            logger.info("Bot info set: id=%s name=%s", self._bot_id, self._bot_name)

    def _auto_reply_allowed(
        self,
        *,
        thread_id: str,
        channel_type: int,
        bot_id: str,
        bot_name: str,
        mentions: dict[str, str],
    ) -> bool:
        cfg = self._bot_config
        if cfg is None:
            return False
        last_reply = self._last_auto_reply_at.get(thread_id, 0.0)
        cooldown_elapsed = time.monotonic() - last_reply >= cfg.auto_reply_cooldown
        return should_allow_auto_reply(
            channel_type=channel_type,
            mentions=mentions,
            bot_id=bot_id,
            bot_name=bot_name,
            auto_reply_enabled=cfg.auto_reply,
            cooldown_elapsed=cooldown_elapsed,
            random_value=self._random.random(),
            rate=cfg.auto_reply_random_rate,
        )

    async def handle(self, event: EventBody) -> None:
        """Normalize a Satori event and enqueue it for processing."""
        if event.message is None or event.message.content is None:
            return
        raw_content = event.message.content
        if not raw_content.strip():
            return

        platform = event.platform or "unknown"
        guild_id = event.guild.id if event.guild else ""
        channel_id = event.channel.id if event.channel else ""
        user_id = event.user.id if event.user else ""
        user_name = ""
        if event.user:
            user_name = event.user.nick or event.user.name or event.user.id or ""
        thread_id = f"{platform}:{guild_id}:{channel_id}"
        channel_type = int(event.channel.type) if event.channel else 0
        parsed = parse_content(raw_content)
        message = IncomingMessage(
            event_id=f"{platform}:{event.id}:{event.message.id}",
            platform=platform,
            guild_id=guild_id,
            thread_id=thread_id,
            channel_id=channel_id,
            channel_type=channel_type,
            user_id=user_id,
            user_name=user_name,
            raw_content=raw_content,
            content_kind=parsed.kind.value,
            has_text=parsed.has_text,
            llm_text=parsed.llm_text,
            clean_text=parsed.clean_text,
            mentions=parsed.mentions,
            image_srcs=[a.src for a in parsed.attachments if a.type == "img"],
        )
        await self._queue.put(message)

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------

    async def _worker(self) -> None:
        """Background worker: dequeue and process messages.

        Per-thread_id locks serialize same-conversation messages to
        prevent LangGraph checkpoint conflicts.
        """
        while True:
            try:
                item = await self._queue.get()
                if item is None:
                    self._queue.task_done()
                    return
                lock = self._locks.setdefault(item.thread_id, asyncio.Lock())
                async with lock:
                    try:
                        await self._process(item)
                    except Exception:
                        logger.exception(
                            "Message processing failed for thread %s", item.thread_id
                        )
                self._queue.task_done()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Message worker loop error")

    async def _process(self, message: IncomingMessage) -> None:
        """Route and execute a normalized incoming message."""
        auto_reply_allowed = self._auto_reply_allowed(
            thread_id=message.thread_id,
            channel_type=message.channel_type,
            bot_id=self._bot_id or "",
            bot_name=self._bot_name or "",
            mentions=message.mentions,
        )
        decision = route_incoming(
            message,
            command_registry=self._command_registry,
            command_enabled=bool(
                self._bot_config is not None and self._bot_config.command_enabled
            ),
            command_prefix=self._bot_config.command_prefix if self._bot_config else "/",
            bot_id=self._bot_id or "",
            bot_name=self._bot_name or "",
            auto_reply_allowed=auto_reply_allowed,
            admin_ids=tuple(self._bot_config.admin_ids) if self._bot_config else (),
        )

        if decision.action == RouteAction.COMMAND:
            await self._execute_command(message, decision)
            return
        if decision.action == RouteAction.IGNORE:
            return
        if self.graph is None:
            return
        if self._compactor is not None:
            await self._compactor.compact_if_needed(message.thread_id)

        human = self._build_human_message(message)
        if decision.action == RouteAction.CONTEXT_ONLY:
            thread_config = {"configurable": {"thread_id": message.thread_id}}
            await self.graph.aupdate_state(thread_config, {"messages": [human]})
            await self._enqueue_index(message, "")
            return

        await self._run_reply_graph(message, human, auto_reply_allowed)

    # ------------------------------------------------------------------
    # Command execution
    # ------------------------------------------------------------------

    async def _execute_command(self, message: IncomingMessage, decision) -> None:
        command = decision.command
        actor = decision.actor
        if command is None or actor is None:
            return
        if not can_run(command, actor):
            reply_text = "无权执行该指令。"
        elif decision.parsed_command is not None and decision.parsed_command.error:
            reply_text = f"指令参数错误，用法：{command.usage}"
        else:
            ctx = CommandContext(
                raw=message.raw_content,
                actor=actor,
                platform=message.platform,
                guild_id=message.guild_id,
                channel_id=message.channel_id,
                thread_id=message.thread_id,
                channel_type=message.channel_type,
                args=decision.parsed_command.args if decision.parsed_command else (),
                config=self._bot_config,
                services=self._command_services,
            )
            reply_text = (await run_command(command, ctx)).text
        logger.info(
            "Command /%s by %s (admin=%s, thread=%s)",
            command.name, message.user_id, actor.is_admin, message.thread_id,
        )
        if reply_text:
            await self._send_reply(message.channel_id, reply_text)

    # ------------------------------------------------------------------
    # Reply graph
    # ------------------------------------------------------------------

    def _build_human_message(self, message: IncomingMessage) -> HumanMessage:
        if message.channel_type != ChannelType.DIRECT and message.user_name:
            return HumanMessage(content=message.llm_text, name=message.user_name)
        return HumanMessage(content=message.llm_text)

    def _build_graph_input(
        self,
        message: IncomingMessage,
        human: HumanMessage,
        auto_reply_allowed: bool,
    ) -> dict:
        return {
            "thread_id": message.thread_id,
            "channel_id": message.channel_id,
            "persona": self._persona,
            "reply_text": "",
            "should_respond": True,
            "bot_name": self._bot_name or "",
            "bot_id": self._bot_id or "",
            "tool_rounds": 0,
            "user_id": message.user_id,
            "channel_type": message.channel_type,
            "user_name": message.user_name,
            "content_kind": message.content_kind,
            "has_text": message.has_text,
            "llm_text": message.llm_text,
            "clean_text": message.clean_text,
            "mentions": message.mentions,
            "image_srcs": message.image_srcs,
            "auto_reply": auto_reply_allowed,
            "messages": [human],
        }

    async def _run_reply_graph(
        self,
        message: IncomingMessage,
        human: HumanMessage,
        auto_reply_allowed: bool,
    ) -> None:
        max_rounds = (
            self._bot_config.rag_max_agent_rounds
            if self._bot_config is not None
            else 3
        )
        recursion_limit = 2 * max_rounds + 8
        try:
            result = await self.graph.ainvoke(
                self._build_graph_input(message, human, auto_reply_allowed),
                {
                    "configurable": {"thread_id": message.thread_id},
                    "recursion_limit": recursion_limit,
                },
            )
        except Exception:
            logger.exception("Graph invoke failed for thread %s", message.thread_id)
            return

        reply_text = result.get("reply_text", "")
        if reply_text:
            await self._send_reply(message.channel_id, reply_text)
        if reply_text and auto_reply_allowed:
            self._last_auto_reply_at[message.thread_id] = time.monotonic()
        await self._enqueue_index(message, reply_text)

    # ------------------------------------------------------------------
    # Background indexing
    # ------------------------------------------------------------------

    async def _enqueue_index(self, message: IncomingMessage, reply_text: str) -> None:
        if self._index_worker is None:
            return
        task = self._build_index_task(message, reply_text)
        if task is None:
            return
        await self._index_worker.enqueue(task)

    def _build_index_task(
        self, message: IncomingMessage, reply_text: str
    ) -> IndexTurnTask | None:
        user_message = message.clean_text
        reply_text = content_to_text(reply_text).strip()
        if (
            message.content_kind == MessageKind.IMAGE.value
            and (user_message.strip() or reply_text)
        ):
            user_message = f"{user_message} {IMAGE_PLACEHOLDER}".strip()
        if not user_message.strip() and not reply_text:
            return None
        return IndexTurnTask(
            thread_id=message.thread_id,
            user_id=message.user_id,
            user_name=message.user_name,
            bot_id=self._bot_id or "",
            bot_name=self._bot_name or "",
            user_message=user_message,
            bot_reply=reply_text,
        )

    # ------------------------------------------------------------------
    # Reply sending
    # ------------------------------------------------------------------

    async def _send_reply(self, channel_id: str, content: str) -> None:
        """Send reply text to the source channel via Satori HTTP API."""
        try:
            await self._api_client.send_message(channel_id, content)
        except Exception:
            logger.exception("Failed to send reply to channel %s", channel_id)
