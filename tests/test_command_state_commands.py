"""/clear /compact 与真实 LangGraph checkpoint 的集成测试。"""

import asyncio

from langchain_core.messages import AIMessage

from bot.core.commands import (
    CommandActor,
    CommandContext,
    CommandServices,
    build_command_registry,
)
from bot.core.graph import create_graph
from bot.core.skills import Skill, SkillRegistry
from common import BotConfig
from tests.fakes import ScriptedLLM, make_state


def _ctx(services, thread_id="t1", config=None):
    return CommandContext(
        raw="/clear",
        actor=CommandActor(user_id="admin1", name="admin", is_admin=True),
        platform="test",
        guild_id="",
        channel_id="c1",
        thread_id=thread_id,
        channel_type=1,
        args=(),
        config=config or BotConfig(_env_file=None, admin_ids=["admin1"]),
        services=services,
    )


def test_clear_deletes_thread_checkpoint_and_active_skills(tmp_path):
    async def run():
        skill_registry = SkillRegistry({
            "translate": Skill(name="translate", description="翻译", body="规则"),
        })
        llm = ScriptedLLM([
            AIMessage(content="", tool_calls=[
                {"name": "load_skill", "args": {"skill_name": "translate"},
                 "id": "call_skill", "type": "tool_call"},
            ]),
            AIMessage(content="已加载翻译技能"),
        ])
        graph, checkpointer = await create_graph(
            llm,
            BotConfig(_env_file=None),
            db_dir=str(tmp_path),
            skill_registry=skill_registry,
        )
        try:
            cfg = {"configurable": {"thread_id": "t1"}}
            state = make_state(
                thread_id="t1",
                channel_id="c1",
                channel_type=1,
                llm_text="加载翻译技能",
                clean_text="加载翻译技能",
            )
            result = await graph.ainvoke(state, cfg)
            assert result["active_skills"] == ["translate"]

            services = CommandServices(
                version="test", started_at=0.0, bot_name="",
                graph=graph, checkpointer=checkpointer,
            )
            registry = build_command_registry(services)
            reply = await registry.resolve("clear").handler(_ctx(services))

            assert reply.text == "已清空当前会话上下文，已加载技能也已清除。"
            snapshot = await graph.aget_state(cfg)
            assert snapshot.values.get("messages", []) == []
            assert not snapshot.values.get("active_skills", [])
        finally:
            await checkpointer.conn.close()

    asyncio.run(run())


def test_compact_force_summarizes_checkpoint(tmp_path):
    async def run():
        llm = ScriptedLLM([
            AIMessage(content="这是第一轮回复"),
            AIMessage(content="这是第二轮回复"),
            AIMessage(content="这是第三轮回复"),
            AIMessage(content="压缩后的摘要"),
        ])
        config = BotConfig(
            _env_file=None,
            llm_context_window=1000,
            summary_trigger_ratio=0.5,
            summary_keep_ratio=0.01,
        )
        graph, checkpointer = await create_graph(
            llm, config, db_dir=str(tmp_path),
        )
        try:
            cfg = {"configurable": {"thread_id": "t1"}}
            for text in (
                "请记住第一个较长的背景信息，后面还会继续讨论",
                "请记住第二个较长的背景信息，也需要保留",
                "请记住第三个较长的背景信息，后续会用上",
            ):
                state = make_state(
                    thread_id="t1",
                    channel_id="c1",
                    channel_type=1,
                    llm_text=text,
                    clean_text=text,
                )
                await graph.ainvoke(state, cfg)
            before = await graph.aget_state(cfg)
            assert len(before.values["messages"]) >= 6

            services = CommandServices(
                version="test", started_at=0.0, bot_name="",
                llm=llm, graph=graph, checkpointer=checkpointer,
            )
            registry = build_command_registry(services)
            reply = await registry.resolve("compact").handler(
                _ctx(services, config=config)
            )

            assert "已提前压缩上下文" in reply.text
            after = await graph.aget_state(cfg)
            assert after.values["conversation_summary"] == "压缩后的摘要"
            assert len(after.values["messages"]) < len(before.values["messages"])
        finally:
            await checkpointer.conn.close()

    asyncio.run(run())
