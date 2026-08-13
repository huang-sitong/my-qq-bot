import logging

from langgraph.graph.state import CompiledStateGraph as CompiledGraph

from bot.core.commands import CommandRegistry, CommandServices
from bot.core.compaction import ContextCompactor
from bot.core.dispatcher import MessageDispatcher
from bot.core.ingress import SatoriMessageIngress
from bot.core.rag.index_worker import IndexWorker
from bot.core.worker import MessageWorkerPool
from bot.transport.http.client import SatoriApiClient
from bot.transport.websocket.client import SatoriClient
from domain.bot.identity import BotIdentity
from domain.satori import EventBody, LoginList

logger = logging.getLogger(__name__)


class MessageHandler:
    """Protocol adapter facade: ingress -> queue/worker -> router -> dispatcher."""

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
        self._api_client = api_client
        self._command_services = command_services
        self._identity = BotIdentity()
        self._ingress = SatoriMessageIngress()
        self._dispatcher = MessageDispatcher(
            graph=graph,
            persona=persona,
            api_client=api_client,
            bot_config=bot_config,
            command_registry=command_registry,
            command_services=command_services,
            compactor=compactor,
            index_worker=index_worker,
            identity=self._identity,
            on_auto_reply_sent=self._mark_auto_reply_sent,
        )
        self._worker_pool = MessageWorkerPool(
            self._dispatcher,
            bot_config=bot_config,
            command_registry=command_registry,
            identity=self._identity,
            worker_count=worker_count,
            queue_maxsize=queue_maxsize,
        )

    @property
    def _worker_tasks(self):
        return self._worker_pool.worker_tasks

    @property
    def _random(self):
        return self._worker_pool.random

    @_random.setter
    def _random(self, value) -> None:
        self._worker_pool.random = value

    @property
    def _last_auto_reply_at(self):
        return self._worker_pool.last_auto_reply_at

    @_last_auto_reply_at.setter
    def _last_auto_reply_at(self, value) -> None:
        self._worker_pool.last_auto_reply_at = value

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        await self._worker_pool.start()

    async def stop(self) -> None:
        await self._worker_pool.stop()

    # ------------------------------------------------------------------
    # Protocol events
    # ------------------------------------------------------------------

    async def handle_login(self, login_list: LoginList) -> None:
        """Extract bot user id and name from the login event."""
        logins = login_list.logins
        if not logins:
            return
        user = logins[0].user
        if user is not None:
            self._identity.id = user.id
            self._identity.name = user.name or user.nick or user.id
            self._api_client.set_user_id(self._identity.id)
            if self._command_services is not None:
                self._command_services.bot_name = self._identity.name
            logger.info("Bot info set: id=%s name=%s", self._identity.id, self._identity.name)

    async def handle(self, event: EventBody) -> None:
        """Normalize a Satori event and enqueue it for processing."""
        message = self._ingress.normalize(event)
        if message is None:
            return
        await self._worker_pool.enqueue(message)

    def _mark_auto_reply_sent(self, thread_id: str) -> None:
        self._worker_pool.mark_reply_sent(thread_id)
