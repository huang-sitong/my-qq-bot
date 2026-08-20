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
from collections import deque
from collections.abc import Callable

from bot.package.conversation.identity import BotIdentity
from bot.package.conversation.message import IncomingMessage
from bot.package.conversation.policy import ReplyPolicy
from bot.package.conversation.router import RouteAction, RouteDecision
from bot.package.domain.ports import MessageQueue, MessageRouter, MessageSink
from bot.package.pipeline.router import route_incoming
from bot.package.utils.logging import trace_context
from bot.package.utils.queue import InMemoryMessageQueue

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
        dispatcher: MessageSink,
        *,
        router: MessageRouter | None = None,
        bot_config=None,
        command_registry=None,
        identity: BotIdentity | None = None,
        worker_count: int = 1,
        queue_maxsize: int = 0,
        batch_max: int = 4,
        queue_factory: Callable[[int], MessageQueue] | None = None,
        dedup_size: int = 0,
        idle_ttl: float = 3600,
        cleanup_interval: float = 300,
    ) -> None:
        self._dispatcher = dispatcher
        self._router = router or route_incoming
        self._bot_config = bot_config
        self._command_registry = command_registry
        self._identity = identity or BotIdentity()
        self._worker_count = worker_count
        self._batch_max = max(batch_max, 0)
        self._queue: MessageQueue = (queue_factory or InMemoryMessageQueue)(
            maxsize=queue_maxsize
        )
        self._locks: dict[str, asyncio.Lock] = {}
        self._worker_tasks: list[asyncio.Task[None]] = []
        self._last_auto_reply_at: dict[str, float] = {}
        self._random = random.Random()
        self._dedup_enabled = dedup_size > 0
        self._seen_event_ids: set[str] = set()
        self._seen_event_order: deque[str] = deque(
            maxlen=dedup_size if self._dedup_enabled else 0
        )
        self._idle_ttl = idle_ttl
        self._cleanup_interval = cleanup_interval
        self._lock_last_used: dict[str, float] = {}
        self._last_cleanup_at = 0.0
        self._processed_count = 0
        self._dropped_count = 0
        self._processing_seconds = 0.0
        self._stage_seconds: dict[str, float] = {"route": 0.0, "dispatch": 0.0}
        self._stage_counts: dict[str, int] = {"route": 0, "dispatch": 0}

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

    @property
    def metrics(self) -> dict[str, int | float]:
        """返回轻量运行时指标，便于 /status 或监控系统采集。"""
        return {
            "queue_size": self._queue.qsize(),
            "processed": self._processed_count,
            "dropped_duplicates": self._dropped_count,
            "active_threads": len(self._locks),
            "avg_processing_seconds": (
                self._processing_seconds / self._processed_count
                if self._processed_count
                else 0.0
            ),
            "avg_route_seconds": (
                self._stage_seconds["route"] / self._stage_counts["route"]
                if self._stage_counts["route"]
                else 0.0
            ),
            "avg_dispatch_seconds": (
                self._stage_seconds["dispatch"] / self._stage_counts["dispatch"]
                if self._stage_counts["dispatch"]
                else 0.0
            ),
        }

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

    async def enqueue(self, message: IncomingMessage) -> bool:
        """Enqueue a normalized message, optionally dropping duplicate ``event_id``.

        Dedup is disabled by default (``dedup_size=0``) for backward
        compatibility; set ``dedup_size > 0`` to enable a bounded idempotency
        window. Returns ``True`` when the message is accepted, ``False`` when it
        is recognized as a duplicate and ignored.
        """
        if self._dedup_enabled:
            if message.event_id in self._seen_event_ids:
                self._dropped_count += 1
                logger.debug("Duplicate event ignored: %s", message.event_id)
                return False
            if len(self._seen_event_order) >= self._seen_event_order.maxlen:
                old = self._seen_event_order.popleft()
                self._seen_event_ids.discard(old)
            self._seen_event_order.append(message.event_id)
            self._seen_event_ids.add(message.event_id)
        await self._queue.put(message)
        return True

    def mark_reply_sent(self, thread_id: str) -> None:
        self._last_auto_reply_at[thread_id] = time.monotonic()

    def _auto_reply_allowed(self, message: IncomingMessage) -> bool:
        cfg = self._bot_config
        if cfg is None:
            return False
        last_reply = self._last_auto_reply_at.get(message.thread_id, 0.0)
        cooldown_elapsed = time.monotonic() - last_reply >= cfg.auto_reply_cooldown
        return ReplyPolicy.should_allow_auto_reply(
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
        decision = self._router(
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
        self._processed_count += 1
        route_start = time.perf_counter()
        decision, auto_reply_allowed = self._route(message)
        self._stage_seconds["route"] += time.perf_counter() - route_start
        self._stage_counts["route"] += 1

        dispatch_start = time.perf_counter()
        await self._dispatcher.dispatch(
            message,
            decision,
            auto_reply_allowed=auto_reply_allowed,
        )
        self._stage_seconds["dispatch"] += time.perf_counter() - dispatch_start
        self._stage_counts["dispatch"] += 1

    async def _process_batch(self, messages: list[IncomingMessage]) -> None:
        """按原位置增量处理一批同 thread 消息。

        遇到命令时先把此前积累的非命令消息投递，再执行命令；命令对配置/状态
        的改动因此会作用于其后的消息。每个 segment 单独容错，单条失败不会
        丢弃批内其余消息。
        """
        self._processed_count += len(messages)
        segment: list[tuple[IncomingMessage, RouteDecision, bool]] = []

        async def flush() -> None:
            nonlocal segment
            if not segment:
                return
            dispatch_start = time.perf_counter()
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
                self._stage_seconds["dispatch"] += time.perf_counter() - dispatch_start
                self._stage_counts["dispatch"] += 1
                segment = []

        for message in messages:
            route_start = time.perf_counter()
            try:
                decision, allowed = self._route(message)
            except Exception:
                logger.exception(
                    "Message routing failed for thread %s", message.thread_id
                )
                continue
            finally:
                self._stage_seconds["route"] += time.perf_counter() - route_start
                self._stage_counts["route"] += 1
            if decision.action == RouteAction.COMMAND:
                await flush()
                dispatch_start = time.perf_counter()
                try:
                    await self._dispatcher.dispatch(
                        message, decision, auto_reply_allowed=allowed,
                    )
                except Exception:
                    logger.exception(
                        "Command dispatch failed for thread %s", message.thread_id
                    )
                finally:
                    self._stage_seconds["dispatch"] += time.perf_counter() - dispatch_start
                    self._stage_counts["dispatch"] += 1
            else:
                segment.append((message, decision, allowed))
        await flush()

    def _maybe_cleanup(self) -> None:
        """Periodically drop idle thread locks and auto-reply timestamps.

        Prevents ``_locks`` / ``_last_auto_reply_at`` from growing without bound
        when the bot talks to many different channels over a long period.
        """
        now = time.monotonic()
        if now - self._last_cleanup_at < self._cleanup_interval:
            return
        self._last_cleanup_at = now
        for thread_id in list(self._locks):
            lock = self._locks[thread_id]
            last_used = self._lock_last_used.get(thread_id, 0.0)
            if not lock.locked() and now - last_used > self._idle_ttl:
                del self._locks[thread_id]
                self._lock_last_used.pop(thread_id, None)
                self._last_auto_reply_at.pop(thread_id, None)

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
                        self._lock_last_used[current.thread_id] = time.monotonic()
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
                        start = time.perf_counter()
                        try:
                            if len(batch) > 1:
                                with trace_context(batch[0].trace_id):
                                    await self._process_batch(batch)
                            else:
                                with trace_context(current.trace_id):
                                    await self._process(current)
                        except Exception:
                            logger.exception(
                                "Message processing failed for thread %s",
                                current.thread_id,
                            )
                        finally:
                            self._processing_seconds += time.perf_counter() - start
                            for _ in batch:
                                self._queue.task_done()
                    self._maybe_cleanup()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Message worker loop error")
