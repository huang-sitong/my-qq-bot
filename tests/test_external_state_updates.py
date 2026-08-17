"""图外状态更新：连续 context-only 批次必须都能写入真实 LangGraph checkpoint。"""

import asyncio

from bot.core.dispatcher import MessageDispatcher
from common import BotConfig
from conversation.identity import BotIdentity
from conversation.message import IncomingMessage
from conversation.router import RouteAction, RouteDecision
from orchestration.graph import create_graph
from tests.fakes import ScriptedLLM


class _NoopApi:
    async def send_message(self, channel_id: str, content: str) -> None:
        pass


def _message(thread_id: str, content: str) -> IncomingMessage:
    return IncomingMessage(
        event_id=f"e-{content}",
        platform="llonebot",
        guild_id="g",
        thread_id=thread_id,
        channel_id="c",
        channel_type=0,
        user_id="u1",
        user_name="u1",
        raw_content=content,
        content_kind="text",
        has_text=True,
        llm_text=content,
        clean_text=content,
        mentions={},
        image_srcs=[],
        trace_id=f"t-{content}",
    )


def test_context_only_batches_append_messages_across_calls(tmp_path):
    async def run():
        graph, checkpointer = await create_graph(
            ScriptedLLM([]),
            BotConfig(_env_file=None, rag_enabled=False),
            db_dir=str(tmp_path),
        )
        try:
            dispatcher = MessageDispatcher(
                graph=graph,
                persona="你是{bot_name}",
                api_client=_NoopApi(),
                bot_config=BotConfig(_env_file=None, rag_enabled=False),
                identity=BotIdentity(id="bot", name="bot"),
            )
            ctx = RouteDecision(action=RouteAction.CONTEXT_ONLY)
            await dispatcher.dispatch_batch(
                [_message("t1", "第一"), _message("t1", "第二")],
                [ctx, ctx],
            )
            await dispatcher.dispatch_batch(
                [_message("t1", "第三")],
                [ctx],
            )
            snapshot = await graph.aget_state(
                {"configurable": {"thread_id": "t1"}}
            )
            assert [m.content for m in snapshot.values["messages"]] == [
                "第一", "第二", "第三",
            ]
        finally:
            await checkpointer.conn.close()

    asyncio.run(run())
