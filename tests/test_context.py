"""build_system_messages：摘要层构造单一来源，call_llm 与 token 估算共用。

层级：persona → 当前时间提示 → 对话摘要。时间提示动态注入（now 可固定供测试）。
"""

from datetime import datetime

from langchain_core.messages import SystemMessage

from bot.core.utils import build_system_messages

FIXED_NOW = datetime(2026, 8, 4, 21, 30, 15)
TIME_HINT = (
    "当前时间：2026-08-04 21:30:15（星期二，本地时区）。\n"
    "涉及“现在/最近/今天/昨天/最近N小时”等相对时间的表述时，以此时间为基准；\n"
    "调用 search_chat_history 的时间参数（hours、start_time、end_time）也按此时间计算。"
)


def test_builds_persona_summary_and_time_layers():
    msgs = build_system_messages("你是助手", "之前聊过猫", now=FIXED_NOW)
    assert [m.content for m in msgs] == ["你是助手", TIME_HINT, "之前的对话摘要：\n之前聊过猫"]
    assert all(isinstance(m, SystemMessage) for m in msgs)


def test_skips_empty_summary():
    msgs = build_system_messages("你是助手", "   ", now=FIXED_NOW)
    assert [m.content for m in msgs] == ["你是助手", TIME_HINT]


def test_skips_empty_persona_keeps_summary():
    msgs = build_system_messages("   ", "摘要", now=FIXED_NOW)
    assert [m.content for m in msgs] == [TIME_HINT, "之前的对话摘要：\n摘要"]


def test_time_hint_shows_current_local_time():
    msgs = build_system_messages("   ", "", now=FIXED_NOW)
    assert [m.content for m in msgs] == [TIME_HINT]  # 时间层无条件注入


def test_estimate_builds_same_layers_as_builder():
    from langchain_core.messages import HumanMessage
    from langchain_core.messages.utils import count_tokens_approximately

    from bot.core.utils import estimate_context_tokens

    msgs = [HumanMessage(content="你好")]
    expected = build_system_messages("你是助手", "摘要", now=FIXED_NOW) + msgs
    # estimate_context_tokens 内部用 build_system_messages 构造层级后 count；
    # 显式构造（固定 now）与内部构造（动态 now，定宽格式字符数相同）→ 估算永不偏离注入
    assert estimate_context_tokens(msgs, "你是助手", "摘要") == count_tokens_approximately(
        expected, chars_per_token=1.5)
