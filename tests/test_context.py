"""build_system_messages：摘要层构造单一来源，call_llm 与 token 估算共用。"""

from langchain_core.messages import SystemMessage

from bot.core.utils import build_system_messages


def test_builds_persona_and_summary_layers():
    msgs = build_system_messages("你是助手", "之前聊过猫")
    assert [m.content for m in msgs] == ["你是助手", "之前的对话摘要：\n之前聊过猫"]
    assert all(isinstance(m, SystemMessage) for m in msgs)


def test_skips_empty_summary():
    msgs = build_system_messages("你是助手", "   ")
    assert [m.content for m in msgs] == ["你是助手"]


def test_skips_empty_persona_keeps_summary():
    msgs = build_system_messages("   ", "摘要")
    assert [m.content for m in msgs] == ["之前的对话摘要：\n摘要"]


def test_empty_both_returns_empty():
    assert build_system_messages("   ", "") == []


def test_estimate_builds_same_layers_as_builder():
    from bot.core.utils import estimate_context_tokens
    from langchain_core.messages import HumanMessage

    msgs = [HumanMessage(content="你好")]
    expected = build_system_messages("你是助手", "摘要") + msgs
    # estimate_context_tokens 内部用 build_system_messages 构造层级后 count；
    # 与显式构造相同消息集合（persona/summary 为空）时应一致 → 估算永不偏离注入
    assert estimate_context_tokens(msgs, "你是助手", "摘要") == estimate_context_tokens(expected, "", "")
