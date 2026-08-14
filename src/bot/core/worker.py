"""消息 worker 池：消费领域消息、路由、投递给 Dispatcher。

同一 thread 的连续消息会被机会式合并成一批（burst 合并），整批只调用一次
Dispatcher，图只跑一次、回复只发一条；命令消息保持原位单独执行，保证
``/clear`` 之类的状态变更发生在后续对话之前。不同 thread 仍靠 per-thread
lock 串行，跨 thread 可并发（worker_count > 1）。
"""

from __future__ import annotations

import asyncio
import logging
import random
import time

from bot.core.dispatcher import MessageDispatcher
from bot.core.router import route_incoming
from bot.core.utils.reply_policy import should_allow_auto_reply
from domain.bot.identity import BotIdentity
from domain.bot.message import IncomingMessage
from domain.bot.router import RouteAction, RouteDecision

logger = logging.getLogger(__name__)


class MessageWorkerPool:
    """按 thread_id 串行消费消息，调用 Router 后交给 Dispatcher。

    每个 worker 取到一条消息后，在持有该 thread 锁期间机会式抽取队列里紧随
    其后的同 thread 消息（上限 ``batch_max``），整批一起路由、一次投递；
    遇到异 thread 消息或停止哨兵即停，其按原顺序稍后单独处理，全局 FIFO
    不被破坏。
    """

    def __init__(
        self,
        dispatcher: MessageDispatcher,
        *,
        bot_config=None,
        command_registry=None,
        identity: BotIdentity | None = None,
        worker_count: int = 1,
        queue_maxsize: int = 0,
        batch_max: int = 4,
    ) -> None:
        self._dispatcher = dispatcher
        self._bot_config = bot_config
        self._command_registry = command_registry
        self._identity = identity or BotIdentity()
        self._worker_count = worker_count
        self._batch_max = max(batch_max, 0)
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

    def _route(self, message: IncomingMessage) -> tuple[RouteDecision, bool]:
        """路由一条消息，返回 (decision, auto_reply_allowed)。"""
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
        return decision, auto_reply_allowed

    async def _process(self, message: IncomingMessage) -> None:
        """Route and dispatch a single normalized incoming message."""
        decision, auto_reply_allowed = self._route(message)
        await self._dispatcher.dispatch(
            message,
            decision,
            auto_reply_allowed=auto_reply_allowed,
        )

    async def _process_batch(self, messages: list[IncomingMessage]) -> None:
        """按原位置增量处理一批同 thread 消息。

        遇到命令时先把此前积累的非命令消息投递，再执行命令；命令对配置/状态
        的改动因此会作用于其后的消息。每个 segment 单独容错，单条失败不会
        丢弃批内其余消息。
        """
        segment: list[tuple[IncomingMessage, RouteDecision, bool]] = []

        async def flush() -> None:
            nonlocal segment
            if not segment:
                return
            try:
                await self._dispatcher.dispatch_batch(
                    [m for m, _, _ in segment],
                    [d for _, d, _ in segment],
                    auto_reply_flags=[allowed for _, _, allowed in segment],
                )
            except Exception:
                logger.exception(
                    "Batch dispatch failed for thread %s",
                    segment[0][0].thread_id,
                )
            finally:
                segment = []

        for message in messages:
            try:
                decision, allowed = self._route(message)
            except Exception:
                logger.exception(
                    "Message routing failed for thread %s", message.thread_id
                )
                continue
            if decision.action == RouteAction.COMMAND:
                await flush()
                try:
                    await self._dispatcher.dispatch(
                        message, decision, auto_reply_allowed=allowed,
                    )
                except Exception:
                    logger.exception(
                        "Command dispatch failed for thread %s", message.thread_id
                    )
            else:
                segment.append((message, decision, allowed))
        await flush()

    async def _worker(self) -> None:
        """Background worker: dequeue and process messages (possibly batched).

        Per-thread_id locks serialize same-conversation messages to
        prevent LangGraph checkpoint conflicts. Within a lock, consecutive
        same-thread messages are drained into one batch; a foreign-thread
        message or the stop sentinel is held and processed right after the
        batch, preserving global FIFO order.
        """
        while True:
            try:
                item = await self._queue.get()
                if item is None:
                    self._queue.task_done()
                    return
                pending: list[IncomingMessage | None] = [item]
                while pending:
                    current = pending.pop(0)
                    if current is None:
                        self._queue.task_done()
                        return
                    lock = self._locks.setdefault(
                        current.thread_id, asyncio.Lock()
                    )
                    async with lock:
                        batch = [current]
                        while len(batch) < self._batch_max:
                            try:
                                nxt = self._queue.get_nowait()
                            except asyncio.QueueEmpty:
                                break
                            if nxt is None or nxt.thread_id != current.thread_id:
                                pending.append(nxt)
                                break
                            batch.append(nxt)
                        try:
                            if len(batch) > 1:
                                await self._process_batch(batch)
                            else:
                                await self._process(current)
                        except Exception:
                            logger.exception(
                                "Message processing failed for thread %s",
                                current.thread_id,
                            )
                        finally:
                            for _ in batch:
                                self._queue.task_done()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Message worker loop error")
