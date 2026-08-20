"""仓库端口与基础设施适配器测试（架构升级第 3 步）。"""

import asyncio

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage

from bot.package.conversation import MessageRecord
from bot.package.domain.repositories import (
    ConversationRepository,
    DocumentRepository,
    MemoryRepository,
)
from bot.package.knowledge import DocumentStore
from bot.package.memory import MemoryStore
from bot.package.orchestration.constants import EXTERNAL_UPDATE_NODE
from bot.package.orchestration.conversation_repository import LangGraphConversationRepository


def _record(**overrides):
    data = {
        "message_id": "m1",
        "thread_id": "t1",
        "user_id": "u1",
        "user_name": "张三",
        "context_text": "你好",
        "index_text": "你好",
        "image_srcs": ("https://img/1.jpg",),
    }
    data.update(overrides)
    return MessageRecord(**data)


def test_domain_repository_ports_exist_and_are_framework_free():
    assert ConversationRepository is not None
    assert DocumentRepository is not None
    assert MemoryRepository is not None


def test_memory_store_exposes_memory_repository_interface():
    required = {
        "load_memories",
        "store_memory",
        "delete_memory",
        "clear_user_memories",
        "format_memories",
        "close",
    }
    assert required <= set(dir(MemoryStore))


def test_document_store_exposes_document_repository_interface():
    required = {
        "add_texts",
        "has_doc",
        "delete_doc",
        "search_dense",
        "search_sparse",
        "close",
    }
    assert required <= set(dir(DocumentStore))


class _FakeGraph:
    def __init__(self, state):
        self.state = state
        self.updates = []

    async def aget_state(self, config):
        return self.state

    async def aupdate_state(self, config, updates, as_node=None):
        self.updates.append((dict(config), updates, as_node))


def test_langgraph_conversation_repository_appends_domain_record():
    async def run():
        graph = _FakeGraph(None)
        repo = LangGraphConversationRepository(graph)
        await repo.append_record(_record(), auto_reply=True)
        assert len(graph.updates) == 1
        config, updates, as_node = graph.updates[0]
        assert config["configurable"]["thread_id"] == "t1"
        assert as_node == EXTERNAL_UPDATE_NODE
        human = updates["messages"][0]
        assert isinstance(human, HumanMessage)
        assert human.content == "你好"
        assert human.additional_kwargs["user_id"] == "u1"
        assert human.additional_kwargs["image_srcs"] == ["https://img/1.jpg"]
        assert human.additional_kwargs["auto_reply"] is True

    asyncio.run(run())


def test_langgraph_conversation_repository_clears_thread_state():
    async def run():
        messages = [
            HumanMessage(content="你好", id="h1"),
            AIMessage(content="你好呀", id="a1"),
        ]
        graph = _FakeGraph(
            type(
                "Snapshot",
                (),
                {"values": {"messages": messages, "conversation_summary": "旧摘要", "active_skills": ["s"], "tool_rounds": 2}},
            )()
        )
        repo = LangGraphConversationRepository(graph)
        await repo.clear("t1")
        _, updates, as_node = graph.updates[0]
        assert as_node == EXTERNAL_UPDATE_NODE
        assert all(isinstance(m, RemoveMessage) for m in updates["messages"])
        assert [m.id for m in updates["messages"]] == ["h1", "a1"]
        assert updates["conversation_summary"] == ""
        assert updates["active_skills"] == []
        assert updates["tool_rounds"] == 0

    asyncio.run(run())
