"""MessageHandler worker pool: multi-worker, per-thread ordering, backpressure."""

import asyncio

from bot.handler import MessageHandler
from domain.satori import Channel, ChannelType, EventBody, Message, User


class _StubApi:
    async def send_message(self, channel_id, content):
        pass


def _make_handler(graph, worker_count=1, queue_maxsize=0):
    return MessageHandler(
        client=object(),
        graph=graph,
        persona="你是{bot_name}",
        api_client=_StubApi(),
        worker_count=worker_count,
        queue_maxsize=queue_maxsize,
    )


def _event(text: str, channel_id: str = "ch1") -> EventBody:
    return EventBody(
        id=hash(text) % 10_000,
        sn=1,
        type="message-created",
        platform="llonebot",
        channel=Channel(id=channel_id, type=ChannelType.DIRECT),
        user=User(id="u1", name="tester"),
        message=Message(id=f"m-{text}", content=text),
    )


class _OrderedGraph:
    def __init__(self):
        self.calls = []

    async def ainvoke(self, state, config):
        await asyncio.sleep(0.01)
        self.calls.append(state["clean_text"])
        return {"reply_text": ""}


class _BlockingGraph:
    def __init__(self):
        self.entered = []
        self.first_entered = asyncio.Event()
        self.release = asyncio.Event()

    async def ainvoke(self, state, config):
        text = state["clean_text"]
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
        handler = _make_handler(graph, worker_count=3)
        await handler.start()
        await handler.handle(_event("m1", "g1"))
        await handler.handle(_event("m2", "g1"))
        await handler.handle(_event("m3", "g1"))
        await handler.stop()
        assert graph.calls == ["m1", "m2", "m3"]

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
        handler = _make_handler(graph, worker_count=2)
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
