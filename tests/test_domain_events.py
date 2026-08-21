"""领域事件与 RAG 索引投影测试（架构升级第 5 步）。"""

import asyncio

import pytest

from bot.package.conversation import (
    ConversationTurnCompleted,
    IncomingMessage,
    MessageRecord,
    RouteAction,
    RouteDecision,
)
from bot.package.domain.events import DomainEvent
from bot.package.knowledge.turn_index_projection import TurnIndexProjection
from bot.package.orchestration.conversation_repository import LangGraphConversationRepository
from bot.package.pipeline.dispatcher import MessageDispatcher
from bot.package.utils.event_bus import InMemoryDomainEventBus


def _message(**overrides):
    data = {
        "event_id": "e1",
        "platform": "qq",
        "guild_id": "g1",
        "thread_id": "qq:g1:c1",
        "channel_id": "c1",
        "channel_type": 1,
        "user_id": "u1",
        "user_name": "张三",
        "raw_content": "你好",
        "content_kind": "text",
        "has_text": True,
        "llm_text": "你好",
        "clean_text": "你好",
        "mentions": {},
        "image_srcs": [],
        "trace_id": "trace-1",
    }
    data.update(overrides)
    return IncomingMessage(**data)


def _event(messages=None, **overrides):
    records = messages or (_message().to_record(),)
    data = {
        "thread_id": "qq:g1:c1",
        "messages": records,
        "bot_id": "bot1",
        "bot_name": "Bot",
        "bot_reply": "收到",
    }
    data.update(overrides)
    return ConversationTurnCompleted(**data)


def test_conversation_turn_completed_is_domain_event_and_validates_boundary():
    event = _event()
    assert isinstance(event, DomainEvent)
    assert event.has_reply is True
    with pytest.raises(ValueError):
        _event(messages=(_message(thread_id="other").to_record(),))


def test_in_memory_event_bus_dispatches_to_subscribers():
    async def run():
        bus = InMemoryDomainEventBus()
        seen = []
        event = _event()

        async def handler(e):
            seen.append(e)

        bus.subscribe(ConversationTurnCompleted, handler)
        await bus.publish(event)
        assert seen == [event]

    asyncio.run(run())


def test_in_memory_event_bus_isolates_handler_errors():
    async def run():
        bus = InMemoryDomainEventBus()
        seen = []

        async def failing(e):
            raise RuntimeError("boom")

        async def ok(e):
            seen.append(e)

        bus.subscribe(ConversationTurnCompleted, failing)
        bus.subscribe(ConversationTurnCompleted, ok)
        await bus.publish(_event())
        assert len(seen) == 1

    asyncio.run(run())


def test_in_memory_event_bus_validates_subscription_type():
    bus = InMemoryDomainEventBus()
    with pytest.raises(TypeError):
        bus.subscribe(str, lambda e: None)


class _FakeIndexWorker:
    def __init__(self):
        self.tasks = []

    async def enqueue(self, task):
        self.tasks.append(task)
        return True


def test_turn_index_projection_builds_one_task_per_message():
    async def run():
        worker = _FakeIndexWorker()
        projection = TurnIndexProjection(worker)
        messages = (
            _message(event_id="e1", trace_id="t1").to_record(),
            _message(
                event_id="e2",
                trace_id="t2",
                content_kind="image",
                has_text=False,
                llm_text="[图片]",
                clean_text="",
                image_srcs=["https://img/1.jpg"],
            ).to_record(),
        )
        await projection.on_turn_completed(_event(messages=messages, bot_reply="收到"))
        assert len(worker.tasks) == 2
        assert worker.tasks[0].trace_id == "t1"
        assert worker.tasks[0].bot_reply == "收到"
        assert worker.tasks[1].trace_id == "t2"
        assert worker.tasks[1].user_message == "[图片]"

    asyncio.run(run())


def test_turn_index_projection_skips_empty_turn():
    async def run():
        worker = _FakeIndexWorker()
        projection = TurnIndexProjection(worker)
        message = _message(
            content_kind="image",
            has_text=False,
            llm_text="[图片]",
            clean_text="",
            image_srcs=[],
        ).to_record()
        # 纯图片无文本且 bot_reply 为空：旧语义为跳过索引
        message = MessageRecord(
            message_id=message.message_id,
            thread_id=message.thread_id,
            user_id=message.user_id,
            user_name=message.user_name,
            context_text=message.context_text,
            index_text=message.index_text,
            image_srcs=message.image_srcs,
            trace_id=message.trace_id,
            content_kind="text",
        )
        await projection.on_turn_completed(_event(messages=(message,), bot_reply=""))
        assert worker.tasks == []

    asyncio.run(run())


class _StubApi:
    def __init__(self):
        self.sent = []

    async def send_message(self, channel_id, content):
        self.sent.append((channel_id, content))


class _StubGraph:
    def __init__(self, reply_text="收到"):
        self.reply_text = reply_text
        self.updates = []

    async def ainvoke(self, state, config):
        return {"reply_text": self.reply_text}

    async def aupdate_state(self, config, updates, as_node=None):
        self.updates.append(updates)


def test_dispatcher_publishes_turn_completed_instead_of_direct_index_enqueue():
    async def run():
        bus = InMemoryDomainEventBus()
        seen = []

        async def record(event):
            seen.append(event)

        bus.subscribe(ConversationTurnCompleted, record)
        graph = _StubGraph()
        dispatcher = MessageDispatcher(
            graph=graph,
            persona="你是{bot_name}",
            api_client=_StubApi(),
            conversation_repository=LangGraphConversationRepository(graph),
            event_bus=bus,
        )
        message = _message()
        await dispatcher.dispatch(
            message,
            RouteDecision(action=RouteAction.REPLY),
        )
        assert len(seen) == 1
        assert seen[0].messages[0].message_id == "e1"
        assert seen[0].bot_reply == "收到"

    asyncio.run(run())


def test_dispatcher_publishes_empty_reply_for_context_only():
    async def run():
        bus = InMemoryDomainEventBus()
        seen = []

        async def record(event):
            seen.append(event)

        bus.subscribe(ConversationTurnCompleted, record)
        graph = _StubGraph()
        dispatcher = MessageDispatcher(
            graph=graph,
            persona="你是{bot_name}",
            api_client=_StubApi(),
            conversation_repository=LangGraphConversationRepository(graph),
            event_bus=bus,
        )
        await dispatcher.dispatch(
            _message(),
            RouteDecision(action=RouteAction.CONTEXT_ONLY),
        )
        assert seen[0].bot_reply == ""
        assert graph.updates

    asyncio.run(run())
