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


def test_skips_empty_persona():
    assert build_system_messages("   ", "摘要") == []
