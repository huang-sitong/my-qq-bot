"""Conversation 聚合根行为测试（架构升级第 4 步）。"""

import pytest

from bot.package.conversation import Conversation, MessageRecord, ReplyDecision


def _conversation(**overrides):
    data = {"thread_id": "qq:g1:c1", "bot_id": "bot1", "bot_name": "Bot"}
    data.update(overrides)
    return Conversation(**data)


def _record(message_id="m1", thread_id="qq:g1:c1", **overrides):
    data = {
        "message_id": message_id,
        "thread_id": thread_id,
        "user_id": "u1",
        "user_name": "张三",
        "context_text": "你好",
    }
    data.update(overrides)
    return MessageRecord(**data)


def test_aggregate_is_frozen_value_snapshot():
    conversation = _conversation()
    updated = conversation.record_tool_call()
    assert updated is not conversation
    assert conversation.tool_rounds == 0
    assert updated.tool_rounds == 1


def test_record_message_enforces_thread_boundary_and_appends():
    conversation = _conversation()
    updated = conversation.record_message(_record())
    assert len(updated.messages) == 1
    assert updated.messages[0].message_id == "m1"
    with pytest.raises(ValueError):
        conversation.record_message(_record(thread_id="other"))


def test_from_message_seeds_first_message_record():
    from bot.package.conversation.message import IncomingMessage

    message = IncomingMessage(
        event_id="e1", platform="qq", guild_id="g1", thread_id="qq:g1:c1",
        channel_id="c1", channel_type=0, user_id="u1", user_name="张三",
        raw_content="你好", content_kind="text", has_text=True,
        llm_text="你好", clean_text="你好", mentions={}, image_srcs=[],
    )
    conversation = Conversation.from_message(message, bot_id="bot1", bot_name="Bot")
    assert conversation.messages[0].message_id == "e1"
    assert conversation.decide(message) == ReplyDecision(should_respond=False, keep_in_context=True)


def test_skill_activation_and_deactivation_are_idempotent():
    conversation = _conversation()
    conversation = conversation.activate_skill("cooking")
    assert conversation.active_skills == ("cooking",)
    assert conversation.activate_skill("cooking") is conversation
    conversation = conversation.activate_skill("reading")
    assert conversation.active_skills == ("cooking", "reading")
    conversation = conversation.deactivate_skill("cooking")
    assert conversation.active_skills == ("reading",)
    assert conversation.deactivate_skill("cooking") is conversation


def test_skill_methods_reject_empty_name():
    conversation = _conversation()
    with pytest.raises(ValueError):
        conversation.activate_skill(" ")
    with pytest.raises(ValueError):
        conversation.deactivate_skill(" ")


def test_clear_context_resets_mutable_context_but_keeps_identity():
    conversation = _conversation(
        messages=(_record(),),
        conversation_summary="旧摘要",
        active_skills=("cooking",),
        tool_rounds=3,
    )
    cleared = conversation.clear_context()
    assert cleared.thread_id == conversation.thread_id
    assert cleared.bot_id == conversation.bot_id
    assert cleared.bot_name == conversation.bot_name
    assert cleared.messages == ()
    assert cleared.conversation_summary == ""
    assert cleared.active_skills == ()
    assert cleared.tool_rounds == 0


def test_tool_rounds_must_not_be_negative():
    with pytest.raises(ValueError):
        _conversation(tool_rounds=-1)
