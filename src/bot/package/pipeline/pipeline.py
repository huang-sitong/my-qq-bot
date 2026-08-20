"""协议无关消息流水线门面。

持有 worker pool 的生命周期，并暴露 enqueue/start/stop/metrics。平台适配器
（当前为 Satori）负责把平台事件归一化为 ``IncomingMessage`` 后调用
:meth:`enqueue`。
"""

from __future__ import annotations

from collections.abc import Callable

from bot.package.conversation.identity import BotIdentity
from bot.package.conversation.message import IncomingMessage
from bot.package.domain.ports import MessageQueue, MessageSink
from bot.package.pipeline.worker import MessageWorkerPool


class MessagePipeline:
    """消息队列 + 路由 + 分发的进程内流水线。

    ``worker_pool`` 是具体并发引擎；本类只做协议无关门面：把
    ``dispatcher``（满足 ``MessageSink`` 协议）接入 worker 池，并负责
    auto_reply 冷却记账户（worker 池）与 dispatcher 之间的回调接线。
    """

    def __init__(
        self,
        dispatcher: MessageSink,
        *,
        router=None,
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
        self.dispatcher = dispatcher
        self.bot_config = bot_config
        self.command_registry = command_registry
        self.identity = identity or BotIdentity()
        self._worker_pool = MessageWorkerPool(
            dispatcher,
            router=router,
            bot_config=bot_config,
            command_registry=command_registry,
            identity=self.identity,
            worker_count=worker_count,
            queue_maxsize=queue_maxsize,
            batch_max=batch_max,
            queue_factory=queue_factory,
            dedup_size=dedup_size,
            idle_ttl=idle_ttl,
            cleanup_interval=cleanup_interval,
        )
        if getattr(dispatcher, "on_auto_reply_sent", None) is None:
            # 回复冷却由 worker pool 统一记账；dispatcher 经公开回调字段接线，
            # 不再触碰私有属性。
            dispatcher.on_auto_reply_sent = self.mark_reply_sent

    @property
    def worker_pool(self) -> MessageWorkerPool:
        return self._worker_pool

    @property
    def metrics(self) -> dict[str, int | float]:
        return self._worker_pool.metrics

    async def start(self) -> None:
        await self._worker_pool.start()

    async def stop(self) -> None:
        await self._worker_pool.stop()

    async def enqueue(self, message: IncomingMessage) -> bool:
        return await self._worker_pool.enqueue(message)

    def mark_reply_sent(self, thread_id: str) -> None:
        self._worker_pool.mark_reply_sent(thread_id)
