import logging

from bot.core.nodes import summarize_node
from bot.core.utils import estimate_context_tokens

logger = logging.getLogger(__name__)


class ContextCompactor:
    def __init__(self, graph, llm, config, skill_registry=None):
        self._graph = graph
        self._llm = llm
        self._config = config
        self._skill_registry = skill_registry

    async def compact_if_needed(self, thread_id: str) -> int:
        thread_config = {"configurable": {"thread_id": thread_id}}
        snapshot = await self._graph.aget_state(thread_config)
        if snapshot is None:
            return 0
        state = snapshot.values
        if not state.get("messages"):
            return 0
        total = estimate_context_tokens(
            state["messages"],
            state.get("persona", ""),
            state.get("conversation_summary", ""),
            skill_registry=self._skill_registry,
            active_skills=state.get("active_skills", []),
        )
        trigger = int(self._config.summary_trigger_ratio * self._config.llm_context_window)
        if total <= trigger:
            return 0
        return await self._compact_state(state, thread_config)

    async def force_compact(self, thread_id: str) -> int:
        thread_config = {"configurable": {"thread_id": thread_id}}
        snapshot = await self._graph.aget_state(thread_config)
        if snapshot is None:
            return 0
        return await self._compact_state(snapshot.values, thread_config)

    async def _compact_state(self, state: dict, thread_config: dict) -> int:
        try:
            result = await summarize_node(
                state,
                llm=self._llm,
                bot_config=self._config,
                skill_registry=self._skill_registry,
                force=True,
            )
        except Exception:
            logger.exception("Context compaction failed for thread %s", state.get("thread_id", ""))
            return 0
        if not result:
            return 0
        await self._graph.aupdate_state(thread_config, result)
        return len(result.get("messages", []))
