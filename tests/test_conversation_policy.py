"""会话领域策略（conversation/policy）测试。

回复/入上下文/auto_reply 业务规则唯一源在会话领域；旧 utils 兼容垫片已删除。
"""

from pathlib import Path

import pytest

from bot.package.conversation import (
    DIRECT_CHANNEL_TYPE,
    NON_REPLY_KINDS,
    Conversation,
    MessageRecord,
    ReplyDecision,
    ReplyPolicy,
)
from bot.package.conversation.message import IncomingMessage
from bot.package.platform.satori import ChannelType


def _message(
    *,
    thread_id: str = "qq:g1:c1",
    channel_type: int = ChannelType.TEXT,
    content_kind: str = "text",
    has_text: bool = True,
    mentions: dict[str, str] | None = None,
) -> IncomingMessage:
    return IncomingMessage(
        event_id="e1",
        platform="qq",
        guild_id="g1",
        thread_id=thread_id,
        channel_id="c1",
        channel_type=channel_type,
        user_id="u1",
        user_name="张三",
        raw_content="hello",
        content_kind=content_kind,
        has_text=has_text,
        llm_text="hello",
        clean_text="hello",
        mentions=mentions or {},
        image_srcs=[],
    )


def test_legacy_utils_policy_shims_are_removed():
    import importlib

    with pytest.raises(ImportError):
        importlib.import_module("bot.package.utils.routing")
    with pytest.raises(ImportError):
        importlib.import_module("bot.package.utils.reply_policy")


def test_platform_direct_channel_constant_re_exports_domain_value():
    from bot.package.platform.satori.constants import DIRECT_CHANNEL_TYPE as satori_direct

    assert satori_direct is DIRECT_CHANNEL_TYPE
    assert DIRECT_CHANNEL_TYPE == ChannelType.DIRECT


def test_non_reply_kinds_match_content_kinds():
    assert NON_REPLY_KINDS == {"file", "audio", "video"}


def test_reply_policy_methods_match_existing_decision_table():
    assert ReplyPolicy.is_explicit_request(ChannelType.DIRECT, "bot1", "Bot", {}) is True
    assert ReplyPolicy.decide_reply(
        ChannelType.TEXT, "file", "bot1", "Bot", {}, auto_reply=True,
    ) is False
    assert ReplyPolicy.decide_reply(
        ChannelType.DIRECT, "text", "bot1", "Bot", {},
    ) is True
    assert ReplyPolicy.keep_in_context(False, "image", has_text=True) is True


def test_evaluate_group_mention_reply_is_kept():
    decision = ReplyPolicy.evaluate(
        _message(mentions={"bot1": "Bot"}),
        bot_id="bot1",
        bot_name="Bot",
    )
    assert decision == ReplyDecision(should_respond=True, keep_in_context=True)


def test_evaluate_group_media_without_text_is_dropped():
    decision = ReplyPolicy.evaluate(
        _message(content_kind="image", has_text=False),
        bot_id="bot1",
        bot_name="Bot",
    )
    assert decision == ReplyDecision(should_respond=False, keep_in_context=False)


def test_policy_module_has_no_framework_or_platform_imports():
    src = (
        Path(__file__).resolve().parents[1]
        / "src/bot/package/conversation/policy.py"
    ).read_text(encoding="utf-8")
    for forbidden in ("langchain", "langgraph", "bot.package.platform", "bot.package.utils"):
        assert forbidden not in src, f"conversation/policy.py must not import {forbidden}"


def test_pipeline_consumes_domain_policy_not_utils_compat_shims():
    repo_root = Path(__file__).resolve().parents[1]
    router_src = (repo_root / "src/bot/package/pipeline/router.py").read_text(encoding="utf-8")
    worker_src = (repo_root / "src/bot/package/pipeline/worker.py").read_text(encoding="utf-8")
    assert "from bot.package.conversation.conversation import Conversation" in router_src
    assert "from bot.package.conversation.policy import ReplyPolicy" in worker_src
    for src in (router_src, worker_src):
        assert "bot.package.utils.routing" not in src
        assert "bot.package.utils.reply_policy" not in src


def test_message_record_is_pure_framework_agnostic_value_object():
    record = MessageRecord(
        message_id="e1",
        thread_id="qq:g1:c1",
        user_id="u1",
        user_name="张三",
        context_text="[图片]",
        index_text="",
    )
    assert record.is_user is True
    assert record.is_assistant is False
    assert record.context_text == "[图片]"
    assert record.index_text == ""


def test_message_record_from_incoming_preserves_dual_texts():
    message = _message()
    record = message.to_record()
    assert record.message_id == message.event_id
    assert record.thread_id == message.thread_id
    assert record.context_text == message.llm_text
    assert record.index_text == message.clean_text


def test_message_record_enforces_identity_and_role_invariants():
    with pytest.raises(ValueError):
        MessageRecord(message_id="", thread_id="t", user_id="u", user_name="", context_text="x")
    with pytest.raises(ValueError):
        MessageRecord(message_id="m", thread_id="", user_id="u", user_name="", context_text="x")
    with pytest.raises(ValueError):
        MessageRecord(
            message_id="m", thread_id="t", user_id="u", user_name="",
            context_text="x", role="system",
        )


def test_conversation_decide_delegates_to_reply_policy():
    conversation = Conversation(thread_id="qq:g1:c1", bot_id="bot1", bot_name="Bot")
    decision = conversation.decide(_message(mentions={"bot1": "Bot"}))
    assert decision == ReplyDecision(should_respond=True, keep_in_context=True)


def test_conversation_rejects_message_from_another_thread():
    conversation = Conversation(thread_id="qq:g1:c1", bot_id="bot1", bot_name="Bot")
    other = _message(thread_id="qq:g1:c2")
    with pytest.raises(ValueError):
        conversation.decide(other)


def test_new_conversation_domain_modules_have_no_framework_imports():
    repo_root = Path(__file__).resolve().parents[1]
    for name in ("policy", "record", "conversation"):
        src = (
            repo_root / f"src/bot/package/conversation/{name}.py"
        ).read_text(encoding="utf-8")
        for forbidden in ("langchain", "langgraph", "bot.package.platform", "bot.package.utils"):
            assert forbidden not in src, f"conversation/{name}.py must not import {forbidden}"
