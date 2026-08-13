import asyncio

from bot.core.commands import CommandServices
from bot.core.rag.index_worker import IndexWorker
from bot.handler import MessageHandler
from common import BotConfig
from domain.satori import Channel, ChannelType, EventBody, Message, User
from tests.fakes import StubRagService


class _StubApi:
    def __init__(self):
        self.sent = []

    async def send_message(self, channel_id, content):
        self.sent.append((channel_id, content))


class _StubGraph:
    def __init__(self):
        self.state = None
        self.updates = []

    async def ainvoke(self, state, config):
        self.state = dict(state)
        return {"reply_text": "收到"}

    async def aget_state(self, config):
        return None

    async def aupdate_state(self, config, updates):
        self.updates.append(updates)


def _handler(graph, index_worker=None):
    return MessageHandler(
        client=object(),
        graph=graph,
        persona="你是{bot_name}",
        api_client=_StubApi(),
        bot_config=BotConfig(_env_file=None),
        command_services=CommandServices(version="test", started_at=0.0, bot_name=""),
        index_worker=index_worker,
    )


def _event(text, channel_type=ChannelType.DIRECT):
    return EventBody(
        id=1,
        sn=1,
        type="message-created",
        platform="llonebot",
        channel=Channel(id="c1", type=channel_type),
        user=User(id="u1", name="张三"),
        message=Message(id="m1", content=text),
    )


def test_reply_path_sends_reply_and_enqueues_index():
    async def run():
        rag = StubRagService()
        worker = IndexWorker(rag)
        await worker.start()
        graph = _StubGraph()
        handler = _handler(graph, index_worker=worker)
        await handler.start()
        await handler.handle(_event("你好"))
        await handler.stop()
        await worker.stop()
        assert handler._api_client.sent == [("c1", "收到")]
        assert rag.last_indexed is not None
        assert rag.last_indexed["bot_reply"] == "收到"

    asyncio.run(run())


def test_context_only_writes_checkpoint_and_indexes():
    async def run():
        rag = StubRagService()
        worker = IndexWorker(rag)
        await worker.start()
        graph = _StubGraph()
        handler = _handler(graph, index_worker=worker)
        await handler.start()
        event = _event(
            "群聊普通发言",
            channel_type=ChannelType.TEXT,
        )
        await handler.handle(event)
        await handler.stop()
        await worker.stop()
        assert graph.updates
        assert rag.last_indexed is not None
        assert rag.last_indexed["bot_reply"] == ""

    asyncio.run(run())


def test_ignore_path_writes_nothing():
    async def run():
        worker = IndexWorker(StubRagService())
        await worker.start()
        graph = _StubGraph()
        handler = _handler(graph, index_worker=worker)
        await handler.start()
        await handler.handle(_event(
            "<img src=\"https://x/1.jpg\"/>",
            channel_type=ChannelType.TEXT,
        ))
        await handler.stop()
        await worker.stop()
        assert graph.state is None
        assert graph.updates == []

    asyncio.run(run())
