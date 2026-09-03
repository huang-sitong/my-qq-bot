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
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from bot.package.config import BotConfig
from bot.package.conversation.identity import BotIdentity
from bot.package.conversation.message import IncomingMessage
from bot.package.conversation.policy import ReplyPolicy
from bot.package.conversation.router import RouteAction, RouteDecision
from bot.package.domain.ports import MessageQueue, MessageRouter, MessageSink
from bot.package.pipeline.router import route_incoming
from bot.package.utils.logging import trace_context
from bot.package.utils.queue import InMemoryMessageQueue

logger = logging.getLogger(__name__)


class _StageClock:
    """分阶段计时器：累计单个阶段的耗时与次数，供 metrics 取平均。"""

    def __init__(self) -> None:
        self._seconds = 0.0
        self._count = 0

    @contextmanager
    def measure(self) -> Iterator[None]:
        start = time.perf_counter()
        try:
            yield
        finally:
            self._seconds += time.perf_counter() - start
            self._count += 1

    @property
    def average_seconds(self) -> float:
        return self._seconds / self._count if self._count else 0.0


class _EventDeduplicator:
    """有界 ``event_id`` 幂等窗口；``window=0`` 表示关闭。"""

    def __init__(self, window: int) -> None:
        self._enabled = window > 0
        self._seen: set[str] = set()
        self._order: deque[str] = deque(maxlen=window if self._enabled else 0)
        self.dropped = 0

    def duplicate(self, event_id: str) -> bool:
        """登记并判断 ``event_id`` 是否为窗口内的重复事件。"""
        if not self._enabled:
            return False
        if event_id in self._seen:
            self.dropped += 1
            return True
        if len(self._order) >= self._order.maxlen:
            self._seen.discard(self._order.popleft())
        self._order.append(event_id)
        self._seen.add(event_id)
        return False


@dataclass
class _RoutedMessage:
    """一条已完成路由的消息及其 auto_reply 判定。"""

    message: IncomingMessage
    decision: RouteDecision
    auto_reply_allowed: bool


@dataclass
class _Segment:
    """批内不可再分的投递单元。

    命令自成一段、长度恒为 1（须在原位单独执行，保证 ``/clear`` 之类的
    状态变更——含路由在内的读取——先于其后消息生效）；非命令消息连续归并
    为 burst 段，整段走一次 ``dispatch_batch``（IGNORE/MEDIA 等由
    Dispatcher 内部过滤）。
    """

    items: list[_RoutedMessage]

    @property
    def is_command(self) -> bool:
        return self.items[0].decision.action == RouteAction.COMMAND


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
        bot_config: BotConfig | None = None,
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
        self._dedup = _EventDeduplicator(dedup_size)
        self._idle_ttl = idle_ttl
        self._cleanup_interval = cleanup_interval
        self._lock_last_used: dict[str, float] = {}
        self._last_cleanup_at = 0.0
        self._processed_count = 0
        self._processing_seconds = 0.0
        self._route_clock = _StageClock()
        self._dispatch_clock = _StageClock()

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
            "dropped_duplicates": self._dedup.dropped,
            "active_threads": len(self._locks),
            "avg_processing_seconds": (
                self._processing_seconds / self._processed_count
                if self._processed_count
                else 0.0
            ),
            "avg_route_seconds": self._route_clock.average_seconds,
            "avg_dispatch_seconds": self._dispatch_clock.average_seconds,
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
        if self._dedup.duplicate(message.event_id):
            logger.debug("Duplicate event ignored: %s", message.event_id)
            return False
        await self._queue.put(message)
        return True

    def mark_reply_sent(self, thread_id: str) -> None:
        self._last_auto_reply_at[thread_id] = time.monotonic()

    def _thread_lock(self, thread_id: str) -> asyncio.Lock:
        return self._locks.setdefault(thread_id, asyncio.Lock())

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
        cfg = self._bot_config
        decision = self._router(
            message,
            command_registry=self._command_registry,
            command_enabled=bool(cfg is not None and cfg.command_enabled),
            command_prefix=cfg.command_prefix if cfg else "/",
            bot_id=self._identity.id,
            bot_name=self._identity.name,
            auto_reply_allowed=auto_reply_allowed,
            admin_ids=tuple(cfg.admin_ids) if cfg else (),
        )
        return decision, auto_reply_allowed

    def _route_message(self, message: IncomingMessage) -> _RoutedMessage | None:
        """路由一条消息；路由异常记日志并返回 ``None``（该消息被跳过）。"""
        try:
            with self._route_clock.measure():
                decision, auto_reply_allowed = self._route(message)
        except Exception:
            logger.exception("Message routing failed for thread %s", message.thread_id)
            return None
        logger.debug(
            "Route decision: thread=%s action=%s trace=%s",
            message.thread_id,
            decision.action.value,
            message.trace_id,
        )
        return _RoutedMessage(message, decision, auto_reply_allowed)

    async def _process(self, message: IncomingMessage) -> None:
        """Route and dispatch a single normalized incoming message."""
        self._processed_count += 1
        logger.debug(
            "Processing message: thread=%s trace=%s",
            message.thread_id,
            message.trace_id,
        )
        routed = self._route_message(message)
        if routed is None:
            return
        try:
            with self._dispatch_clock.measure():
                await self._dispatcher.dispatch(
                    routed.message,
                    routed.decision,
                    auto_reply_allowed=routed.auto_reply_allowed,
                )
        except Exception:
            logger.exception(
                "Single-message dispatch failed for thread %s", message.thread_id
            )

    async def _process_batch(self, messages: list[IncomingMessage]) -> None:
        """按原位置增量处理一批同 thread 消息。

        逐条路由、增量成段：非命令消息连续归并为 burst 段一次投递；遇到
        命令先把此前积累的段投递，再在原位单独执行命令——命令对配置/状态
        的改动（含其后消息的路由读取）因此严格作用于其后消息。路由失败的
        消息单独跳过；各段独立容错，单段失败不丢批内其余消息。
        """
        self._processed_count += len(messages)
        burst: list[_RoutedMessage] = []
        for message in messages:
            routed = self._route_message(message)
            if routed is None:
                continue
            if routed.decision.action != RouteAction.COMMAND:
                burst.append(routed)
                continue
            if burst:
                await self._run_segment(_Segment(burst))
                burst = []
            await self._run_segment(_Segment([routed]))
        if burst:
            await self._run_segment(_Segment(burst))

    async def _run_segment(self, segment: _Segment) -> None:
        """投递一个执行段；命令单发，burst 段整批合并投递。"""
        first = segment.items[0]
        logger.debug(
            "Dispatch segment: thread=%s count=%d command=%s",
            first.message.thread_id,
            len(segment.items),
            segment.is_command,
        )
        try:
            with self._dispatch_clock.measure():
                if segment.is_command:
                    await self._dispatcher.dispatch(
                        first.message,
                        first.decision,
                        auto_reply_allowed=first.auto_reply_allowed,
                    )
                else:
                    await self._dispatcher.dispatch_batch(
                        [item.message for item in segment.items],
                        [item.decision for item in segment.items],
                        auto_reply_flags=[
                            item.auto_reply_allowed for item in segment.items
                        ],
                    )
        except Exception:
            kind = "Command" if segment.is_command else "Batch"
            logger.exception(
                "%s dispatch failed for thread %s", kind, first.message.thread_id
            )

    def _collect_batch(
        self, first: IncomingMessage
    ) -> tuple[list[IncomingMessage], list[IncomingMessage | None]]:
        """从队列抽取与 ``first`` 同 thread 的后继消息组成一批（上限 ``_batch_max``）。

        返回 ``(batch, deferred)``：异 thread 消息或停止哨兵不进批，按遇到
        顺序原样返回，由调用方在本批之后立即处理，全局 FIFO 不被破坏。
        """
        batch = [first]
        deferred: list[IncomingMessage | None] = []
        while len(batch) < self._batch_max:
            try:
                nxt = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if nxt is None or nxt.thread_id != first.thread_id:
                deferred.append(nxt)
                break
            batch.append(nxt)
        return batch, deferred

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
        message or the stop sentinel is deferred and handled right after
        the batch, preserving global FIFO order.
        """
        deferred: deque[IncomingMessage | None] = deque()
        while True:
            try:
                item = deferred.popleft() if deferred else await self._queue.get()
                if item is None:
                    self._queue.task_done()
                    return
                async with self._thread_lock(item.thread_id):
                    self._lock_last_used[item.thread_id] = time.monotonic()
                    batch, blocked = self._collect_batch(item)
                    deferred.extend(blocked)
                    started = time.perf_counter()
                    try:
                        with trace_context(batch[0].trace_id):
                            if len(batch) > 1:
                                await self._process_batch(batch)
                            else:
                                await self._process(batch[0])
                    except Exception:
                        logger.exception(
                            "Message processing failed for thread %s",
                            item.thread_id,
                        )
                    finally:
                        self._processing_seconds += (
                            time.perf_counter() - started
                        )
                        for _ in batch:
                            self._queue.task_done()
                self._maybe_cleanup()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Message worker loop error")
