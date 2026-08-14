"""MessageHandler worker pool: multi-worker, per-thread ordering, backpressure, batching."""

import asyncio

from bot.core.commands import CommandServices, build_command_registry
from bot.handler import MessageHandler
from common import BotConfig
from domain.bot.router import RouteAction
from domain.satori import Channel, ChannelType, EventBody, Message, User


class _StubApi:
    def __init__(self):
        self.sent = []

    async def send_message(self, channel_id, content):
        self.sent.append((channel_id, content))


def _make_handler(graph, worker_count=1, queue_maxsize=0, batch_max=4):
    return MessageHandler(
        client=object(),
        graph=graph,
        persona="你是{bot_name}",
        api_client=_StubApi(),
        worker_count=worker_count,
        queue_maxsize=queue_maxsize,
        batch_max=batch_max,
    )


def _event(
    text: str,
    channel_id: str = "ch1",
    channel_type: ChannelType = ChannelType.DIRECT,
) -> EventBody:
    return EventBody(
        id=hash(text) % 10_000,
        sn=1,
        type="message-created",
        platform="llonebot",
        channel=Channel(id=channel_id, type=channel_type),
        user=User(id="u1", name="tester"),
        message=Message(id=f"m-{text}", content=text),
    )


class _SeqRandom:
    def __init__(self, values):
        self._values = list(values)

    def random(self):
        return self._values.pop(0)


class _OrderedGraph:
    def __init__(self):
        self.calls = []

    async def ainvoke(self, state, config):
        await asyncio.sleep(0.01)
        self.calls.append(
            [msg.content for msg in state["messages"]]
        )
        return {"reply_text": ""}


class _BlockingGraph:
    def __init__(self):
        self.entered = []
        self.first_entered = asyncio.Event()
        self.release = asyncio.Event()

    async def ainvoke(self, state, config):
        text = state["messages"][-1].content
        self.entered.append(text)
        if text == "block":
            self.first_entered.set()
            await self.release.wait()
        return {"reply_text": ""}


def test_worker_pool_starts_and_stops():
    async def run():
        graph = _OrderedGraph()
        handler = _make_handler(graph, worker_count=3)
        await handler.start()
        assert len(handler._worker_tasks) == 3
        await handler.stop()
        assert all(task.done() for task in handler._worker_tasks)

    asyncio.run(run())


def test_same_thread_messages_keep_order_with_multiple_workers():
    async def run():
        graph = _OrderedGraph()
        handler = _make_handler(graph, worker_count=3, batch_max=1)
        await handler.start()
        await handler.handle(_event("m1", "g1"))
        await handler.handle(_event("m2", "g1"))
        await handler.handle(_event("m3", "g1"))
        await handler.stop()
        assert graph.calls == [["m1"], ["m2"], ["m3"]]

    asyncio.run(run())


def test_different_threads_can_run_concurrently():
    async def run():
        graph = _BlockingGraph()
        handler = _make_handler(graph, worker_count=2)
        await handler.start()
        await handler.handle(_event("block", "g1"))
        await graph.first_entered.wait()
        await handler.handle(_event("other", "g2"))
        await asyncio.sleep(0.05)
        assert "other" in graph.entered
        graph.release.set()
        await handler.stop()

    asyncio.run(run())


def test_same_thread_second_message_waits_for_lock():
    async def run():
        graph = _BlockingGraph()
        handler = _make_handler(graph, worker_count=2, batch_max=1)
        await handler.start()
        await handler.handle(_event("block", "g1"))
        await graph.first_entered.wait()
        await handler.handle(_event("later", "g1"))
        await asyncio.sleep(0.05)
        assert graph.entered == ["block"]
        graph.release.set()
        await handler.stop()
        assert graph.entered == ["block", "later"]

    asyncio.run(run())


def test_queue_maxsize_blocks_ingress_until_worker_drains():
    async def run():
        graph = _BlockingGraph()
        handler = _make_handler(graph, worker_count=1, queue_maxsize=1)
        first = asyncio.create_task(handler.handle(_event("first", "g1")))
        await first
        second = asyncio.create_task(handler.handle(_event("later", "g1")))
        await asyncio.sleep(0.05)
        assert not second.done()
        await handler.start()
        await second
        await handler.stop()

    asyncio.run(run())


# ----------------------------------------------------------------------
# Batching: same-thread bursts coalesce into one graph invoke
# ----------------------------------------------------------------------


def test_batch_coalesces_same_thread_burst_into_one_invoke():
    async def run():
        graph = _OrderedGraph()
        handler = _make_handler(graph, worker_count=1, batch_max=4)
        await handler.start()
        for text in ("m1", "m2", "m3"):
            await handler.handle(_event(text, "g1"))
        await handler.stop()
        # 一次图调用，三条消息按序进入同一批
        assert graph.calls == [["m1", "m2", "m3"]]

    asyncio.run(run())


def test_batch_respects_batch_max_and_keeps_order():
    async def run():
        graph = _OrderedGraph()
        handler = _make_handler(graph, worker_count=1, batch_max=2)
        await handler.start()
        for text in ("m1", "m2", "m3", "m4", "m5"):
            await handler.handle(_event(text, "g1"))
        await handler.stop()
        assert graph.calls == [["m1", "m2"], ["m3", "m4"], ["m5"]]

    asyncio.run(run())


def test_batch_stops_at_different_thread_and_preserves_fifo():
    async def run():
        graph = _OrderedGraph()
        handler = _make_handler(graph, worker_count=1, batch_max=4)
        await handler.start()
        await handler.handle(_event("g1-1", "g1"))
        await handler.handle(_event("g2-1", "g2"))
        await handler.handle(_event("g1-2", "g1"))
        await handler.handle(_event("g2-2", "g2"))
        await handler.stop()
        # g2 消息隔断 g1 的批；异 thread 消息被持有、按原顺序紧随处理
        assert graph.calls == [["g1-1"], ["g2-1"], ["g1-2"], ["g2-2"]]

    asyncio.run(run())


class _ReplyGraph:
    def __init__(self):
        self.calls = []

    async def ainvoke(self, state, config):
        self.calls.append([msg.content for msg in state["messages"]])
        return {"reply_text": "收到"}


def test_batch_sends_single_reply_for_burst():
    async def run():
        graph = _ReplyGraph()
        handler = _make_handler(graph, worker_count=1, batch_max=4)
        await handler.start()
        for text in ("m1", "m2", "m3"):
            await handler.handle(_event(text, "g1"))
        await handler.stop()
        assert graph.calls == [["m1", "m2", "m3"]]
        assert handler._api_client.sent == [("g1", "收到")]

    asyncio.run(run())


def test_batch_max_zero_disables_coalescing():
    async def run():
        graph = _OrderedGraph()
        handler = _make_handler(graph, worker_count=1, batch_max=0)
        await handler.start()
        for text in ("m1", "m2", "m3"):
            await handler.handle(_event(text, "g1"))
        await handler.stop()
        assert graph.calls == [["m1"], ["m2"], ["m3"]]

    asyncio.run(run())


def test_batch_command_applies_to_following_message():
    async def run():
        cfg = BotConfig(
            _env_file=None,
            command_enabled=True,
            admin_ids=["u1"],
            auto_reply=False,
            auto_reply_random_rate=1.0,
            auto_reply_cooldown=0,
        )
        services = CommandServices(version="test", started_at=0.0, bot_name="")
        registry = build_command_registry(services)
        graph = _ReplyGraph()
        api = _StubApi()
        handler = MessageHandler(
            client=object(),
            graph=graph,
            persona="p",
            api_client=api,
            bot_config=cfg,
            command_registry=registry,
            command_services=services,
            batch_max=4,
        )
        await handler.start()
        await handler.handle(_event("/auto_reply on", "g1", ChannelType.TEXT))
        await handler.handle(_event("hello", "g1", ChannelType.TEXT))
        await handler.stop()
        assert graph.calls == [["hello"]]
        assert api.sent == [
            ("g1", "auto_reply 已开启。"),
            ("g1", "收到"),
        ]

    asyncio.run(run())


def test_batch_marks_cooldown_when_any_message_auto_replies():
    async def run():
        cfg = BotConfig(
            _env_file=None,
            auto_reply=True,
            auto_reply_random_rate=0.5,
            auto_reply_cooldown=0,
        )
        graph = _ReplyGraph()
        api = _StubApi()
        handler = MessageHandler(
            client=object(),
            graph=graph,
            persona="p",
            api_client=api,
            bot_config=cfg,
            batch_max=4,
        )
        handler._random = _SeqRandom([0.0, 0.99])
        await handler.start()
        await handler.handle(_event("m1", "g1", ChannelType.TEXT))
        await handler.handle(_event("m2", "g1", ChannelType.TEXT))
        await handler.stop()
        assert api.sent == [("g1", "收到")]
        assert "llonebot::g1" in handler._last_auto_reply_at

    asyncio.run(run())


def test_batch_cooldown_applies_to_following_message():
    async def run():
        cfg = BotConfig(
            _env_file=None,
            auto_reply=True,
            auto_reply_random_rate=1.0,
            auto_reply_cooldown=60,
        )
        graph = _ReplyGraph()
        api = _StubApi()
        handler = MessageHandler(
            client=object(),
            graph=graph,
            persona="p",
            api_client=api,
            bot_config=cfg,
            batch_max=4,
        )
        await handler.handle(_event("m1", "g1", ChannelType.TEXT))
        await handler.handle(_event("m2", "g1", ChannelType.TEXT))
        await handler.start()
        await handler._worker_pool._queue.join()
        await handler.handle(_event('<img src="x.jpg"/>', "g1", ChannelType.TEXT))
        await handler.stop()
        assert graph.calls == [["m1", "m2"]]
        assert api.sent == [("g1", "收到")]

    asyncio.run(run())


def test_batch_command_dispatch_error_does_not_drop_following_message():
    async def run():
        cfg = BotConfig(_env_file=None, command_enabled=True, admin_ids=["u1"])
        services = CommandServices(version="test", started_at=0.0, bot_name="")
        registry = build_command_registry(services)
        graph = _ReplyGraph()
        api = _StubApi()
        handler = MessageHandler(
            client=object(),
            graph=graph,
            persona="p",
            api_client=api,
            bot_config=cfg,
            command_registry=registry,
            command_services=services,
            batch_max=4,
        )
        original_dispatch = handler._dispatcher.dispatch

        async def failing_dispatch(message, decision, *, auto_reply_allowed=False):
            if decision.action == RouteAction.COMMAND:
                raise RuntimeError("boom")
            return await original_dispatch(
                message, decision, auto_reply_allowed=auto_reply_allowed
            )

        handler._dispatcher.dispatch = failing_dispatch
        await handler.start()
        await handler.handle(_event("/ping", "g1"))
        await handler.handle(_event("hello", "g1"))
        await handler.stop()
        assert graph.calls == [["hello"]]
        assert api.sent == [("g1", "收到")]

    asyncio.run(run())
