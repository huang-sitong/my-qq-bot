"""消息 worker 池：消费领域消息、路由、投递给 Dispatcher。"""

from __future__ import annotations

import asyncio
import logging
import random
import time

from bot.core.dispatcher import MessageDispatcher
from bot.core.router import route_incoming
from bot.core.utils.reply_policy import should_allow_auto_reply
from object.bot.identity import BotIdentity
from object.bot.message import IncomingMessage

logger = logging.getLogger(__name__)


class MessageWorkerPool:
    """按 thread_id 串行消费消息，调用 Router 后交给 Dispatcher。"""

    def __init__(
        self,
        dispatcher: MessageDispatcher,
        *,
        bot_config=None,
        command_registry=None,
        identity: BotIdentity | None = None,
        worker_count: int = 1,
        queue_maxsize: int = 0,
    ) -> None:
        self._dispatcher = dispatcher
        self._bot_config = bot_config
        self._command_registry = command_registry
        self._identity = identity or BotIdentity()
        self._worker_count = worker_count
        self._queue: asyncio.Queue[IncomingMessage | None] = asyncio.Queue(
            maxsize=queue_maxsize
        )
        self._locks: dict[str, asyncio.Lock] = {}
        self._worker_tasks: list[asyncio.Task[None]] = []
        self._last_auto_reply_at: dict[str, float] = {}
        self._random = random.Random()

    @property
    def worker_tasks(self) -> list[asyncio.Task[None]]:
        return self._worker_tasks

    @property
    def last_auto_reply_at(self) -> dict[str, float]:
        return self._last_auto_reply_at

    @last_auto_reply_at.setter
    def last_auto_reply_at(self, value: dict[str, float]) -> None:
        self._last_auto_reply_at = value

    @property
    def random(self) -> random.Random:
        return self._random

    @random.setter
    def random(self, value: random.Random) -> None:
        self._random = value

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

    async def enqueue(self, message: IncomingMessage) -> None:
        await self._queue.put(message)

    def mark_reply_sent(self, thread_id: str) -> None:
        self._last_auto_reply_at[thread_id] = time.monotonic()

    def _auto_reply_allowed(self, message: IncomingMessage) -> bool:
        cfg = self._bot_config
        if cfg is None:
            return False
        last_reply = self._last_auto_reply_at.get(message.thread_id, 0.0)
        cooldown_elapsed = time.monotonic() - last_reply >= cfg.auto_reply_cooldown
        return should_allow_auto_reply(
            channel_type=message.channel_type,
            mentions=message.mentions,
            bot_id=self._identity.id,
            bot_name=self._identity.name,
            auto_reply_enabled=cfg.auto_reply,
            cooldown_elapsed=cooldown_elapsed,
            random_value=self._random.random(),
            rate=cfg.auto_reply_random_rate,
        )

    async def _process(self, message: IncomingMessage) -> None:
        """Route and dispatch a normalized incoming message."""
        auto_reply_allowed = self._auto_reply_allowed(message)
        decision = route_incoming(
            message,
            command_registry=self._command_registry,
            command_enabled=bool(
                self._bot_config is not None and self._bot_config.command_enabled
            ),
            command_prefix=(
                self._bot_config.command_prefix if self._bot_config else "/"
            ),
            bot_id=self._identity.id,
            bot_name=self._identity.name,
            auto_reply_allowed=auto_reply_allowed,
            admin_ids=tuple(self._bot_config.admin_ids) if self._bot_config else (),
        )
        await self._dispatcher.dispatch(
            message,
            decision,
            auto_reply_allowed=auto_reply_allowed,
        )

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
