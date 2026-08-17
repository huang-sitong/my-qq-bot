import asyncio
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage

from common import BotConfig
from context.compaction import ContextCompactor
from tests.fakes import ScriptedLLM


class _FakeGraph:
    def __init__(self, values):
        self.values = values
        self.updates = []

    async def aget_state(self, config):
        return SimpleNamespace(values=self.values)

    async def aupdate_state(self, config, updates, as_node=None):
        self.updates.append(updates)


def _state(messages):
    return {
        "messages": messages,
        "persona": "你是{bot_name}",
        "conversation_summary": "",
        "active_skills": [],
    }


def test_compact_if_needed_noop_below_threshold():
    llm = ScriptedLLM([])
    graph = _FakeGraph(_state([HumanMessage(content="hi")]))
    config = BotConfig(_env_file=None, llm_context_window=10_000)
    compactor = ContextCompactor(graph, llm, config)

    removed = asyncio.run(compactor.compact_if_needed("t1"))

    assert removed == 0
    assert graph.updates == []


def test_compact_if_needed_writes_summary_above_threshold():
    llm = ScriptedLLM([AIMessage(content="压缩后的摘要")])
    graph = _FakeGraph(_state([
        HumanMessage(content="x" * 2000),
        AIMessage(content="y" * 2000),
    ]))
    config = BotConfig(
        _env_file=None,
        llm_context_window=100,
        summary_trigger_ratio=0.5,
        summary_keep_ratio=0.01,
    )
    compactor = ContextCompactor(graph, llm, config)

    removed = asyncio.run(compactor.compact_if_needed("t1"))

    assert removed > 0
    assert graph.updates
    assert graph.updates[0]["conversation_summary"] == "压缩后的摘要"


def test_force_compact_noop_when_nothing_removable():
    llm = ScriptedLLM([])
    graph = _FakeGraph(_state([]))
    config = BotConfig(_env_file=None)
    compactor = ContextCompactor(graph, llm, config)

    removed = asyncio.run(compactor.force_compact("t1"))

    assert removed == 0
