"""MessagePipeline 端到端：reply / context_only / ignore / burst 合并与 RAG 索引。"""

import asyncio

from bot.package.commands import CommandServices
from bot.package.config import BotConfig
from bot.package.conversation.events import ConversationTurnCompleted
from bot.package.conversation.identity import BotIdentity
from bot.package.knowledge.index_worker import IndexWorker
from bot.package.knowledge.turn_index_projection import TurnIndexProjection
from bot.package.orchestration.conversation_repository import LangGraphConversationRepository
from bot.package.pipeline.dispatcher import MessageDispatcher
from bot.package.pipeline.pipeline import MessagePipeline
from bot.package.platform.satori import Channel, ChannelType, EventBody, Message, User
from bot.package.platform.satori.ingress import SatoriMessageIngress
from bot.package.utils.event_bus import InMemoryDomainEventBus
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
        self.last_as_node = None

    async def ainvoke(self, state, config):
        self.state = dict(state)
        return {"reply_text": "收到"}

    async def aget_state(self, config):
        return None

    async def aupdate_state(self, config, updates, as_node=None):
        self.last_as_node = as_node
        self.updates.append(updates)


def _pipeline(graph, index_worker=None, batch_max=4):
    """与生产 boot 同构装配：索引经事件总线 + TurnIndexProjection 投影。"""
    identity = BotIdentity()
    api_client = _StubApi()
    config = BotConfig(_env_file=None)
    event_bus = InMemoryDomainEventBus()
    if index_worker is not None:
        event_bus.subscribe(
            ConversationTurnCompleted,
            TurnIndexProjection(index_worker).on_turn_completed,
        )
    dispatcher = MessageDispatcher(
        graph=graph,
        persona="你是{bot_name}",
        api_client=api_client,
        bot_config=config,
        command_services=CommandServices(version="test", started_at=0.0, bot_name=""),
        identity=identity,
        conversation_repository=LangGraphConversationRepository(graph),
        event_bus=event_bus,
    )
    return MessagePipeline(
        dispatcher,
        bot_config=config,
        identity=identity,
        batch_max=batch_max,
    )


def _event(
    text,
    channel_type=ChannelType.DIRECT,
    user_id="u1",
    user_name="张三",
):
    return EventBody(
        id=1,
        sn=1,
        type="message-created",
        platform="llonebot",
        channel=Channel(id="c1", type=channel_type),
        user=User(id=user_id, name=user_name),
        message=Message(id="m1", content=text),
    )


async def _enqueue(pipeline, event):
    message = SatoriMessageIngress().normalize(event)
    assert message is not None
    return await pipeline.enqueue(message)


def test_reply_path_sends_reply_and_enqueues_index():
    async def run():
        rag = StubRagService()
        worker = IndexWorker(rag)
        await worker.start()
        graph = _StubGraph()
        pipeline = _pipeline(graph, index_worker=worker)
        await pipeline.start()
        await _enqueue(pipeline, _event("你好"))
        await pipeline.stop()
        await worker.stop()
        assert pipeline.dispatcher._api_client.sent == [("c1", "收到")]
        assert rag.last_indexed is not None
        assert rag.last_indexed["bot_reply"] == "收到"

    asyncio.run(run())


def test_context_only_writes_checkpoint_and_indexes():
    async def run():
        rag = StubRagService()
        worker = IndexWorker(rag)
        await worker.start()
        graph = _StubGraph()
        pipeline = _pipeline(graph, index_worker=worker)
        await pipeline.start()
        event = _event(
            "群聊普通发言",
            channel_type=ChannelType.TEXT,
        )
        await _enqueue(pipeline, event)
        await pipeline.stop()
        await worker.stop()
        assert graph.updates
        assert graph.last_as_node == "describe_image"
        assert rag.last_indexed is not None
        assert rag.last_indexed["bot_reply"] == ""

    asyncio.run(run())


def test_ignore_path_writes_nothing():
    async def run():
        worker = IndexWorker(StubRagService())
        await worker.start()
        graph = _StubGraph()
        pipeline = _pipeline(graph, index_worker=worker)
        await pipeline.start()
        await _enqueue(pipeline, _event(
            "<img src=\"https://x/1.jpg\"/>",
            channel_type=ChannelType.TEXT,
        ))
        await pipeline.stop()
        await worker.stop()
        assert graph.state is None
        assert graph.updates == []

    asyncio.run(run())


def test_batch_reply_runs_graph_once_and_sends_one_reply():
    async def run():
        rag = StubRagService()
        worker = IndexWorker(rag)
        await worker.start()
        graph = _StubGraph()
        pipeline = _pipeline(graph, index_worker=worker, batch_max=4)
        await pipeline.start()
        for text in ("你好", "在吗", "帮我看看"):
            await _enqueue(pipeline, _event(text))
        await pipeline.stop()
        await worker.stop()
        # 突发合并：一次图调用、一条回复；每条消息都入 RAG 索引
        assert pipeline.dispatcher._api_client.sent == [("c1", "收到")]
        assert len(graph.state["messages"]) == 3
        assert [m.content for m in graph.state["messages"]] == ["你好", "在吗", "帮我看看"]
        assert rag.last_indexed is not None
        assert rag.last_indexed["bot_reply"] == "收到"
        assert rag.last_indexed["user_message"] == "帮我看看"

    asyncio.run(run())


def test_batch_context_only_writes_each_record():
    async def run():
        rag = StubRagService()
        worker = IndexWorker(rag)
        await worker.start()
        graph = _StubGraph()
        pipeline = _pipeline(graph, index_worker=worker, batch_max=4)
        await pipeline.start()
        for text in ("群聊发言一", "群聊发言二"):
            await _enqueue(pipeline, _event(text, channel_type=ChannelType.TEXT))
        await pipeline.stop()
        await worker.stop()
        # 两条 context_only 经会话仓库逐条落 checkpoint（每条一次 aupdate_state）
        assert len(graph.updates) == 2
        assert graph.last_as_node == "describe_image"
        assert [m.content for u in graph.updates for m in u["messages"]] == [
            "群聊发言一", "群聊发言二",
        ]
        assert rag.last_indexed is not None
        assert rag.last_indexed["bot_reply"] == ""

    asyncio.run(run())


def test_batch_disabled_processes_messages_individually():
    async def run():
        rag = StubRagService()
        worker = IndexWorker(rag)
        await worker.start()
        graph = _StubGraph()
        pipeline = _pipeline(graph, index_worker=worker, batch_max=1)
        await pipeline.start()
        for text in ("你好", "在吗"):
            await _enqueue(pipeline, _event(text))
        await pipeline.stop()
        await worker.stop()
        assert pipeline.dispatcher._api_client.sent == [("c1", "收到"), ("c1", "收到")]
        assert len(graph.state["messages"]) == 1

    asyncio.run(run())


def test_batch_human_messages_carry_speaker_and_image_metadata():
    async def run():
        graph = _StubGraph()
        pipeline = _pipeline(graph, batch_max=4)
        await pipeline.start()
        await _enqueue(pipeline, _event("你好", user_id="u1", user_name="甲"))
        await _enqueue(
            pipeline,
            _event(
                '<img src="https://x/1.jpg"/>',
                user_id="u2",
                user_name="乙",
            )
        )
        await pipeline.stop()
        messages = graph.state["messages"]
        assert messages[0].additional_kwargs["user_id"] == "u1"
        assert messages[0].name == "甲"
        assert messages[1].additional_kwargs["user_id"] == "u2"
        assert messages[1].name == "乙"
        assert messages[1].additional_kwargs["image_srcs"] == ["https://x/1.jpg"]

    asyncio.run(run())
