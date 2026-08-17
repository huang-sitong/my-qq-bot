"""build_system_messages：摘要层构造单一来源，call_llm 与 token 估算共用。

层级：persona → 当前时间提示 → 对话摘要。时间提示动态注入（now 可固定供测试）。
"""

from datetime import datetime

from langchain_core.messages import SystemMessage

from context.utils import build_system_messages

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


def test_build_system_messages_normalizes_multimodal_list_summary():
    """回归：多模态 content 块列表作摘要（旧 checkpoint 可能残留）不崩，归一化为纯文本层。"""
    msgs = build_system_messages("你是助手", [{"type": "text", "text": "聊过猫"}], now=FIXED_NOW)
    assert [m.content for m in msgs] == ["你是助手", TIME_HINT, "之前的对话摘要：\n聊过猫"]


def test_time_hint_shows_current_local_time():
    msgs = build_system_messages("   ", "", now=FIXED_NOW)
    assert [m.content for m in msgs] == [TIME_HINT]  # 时间层无条件注入


def test_estimate_builds_same_layers_as_builder():
    from langchain_core.messages import HumanMessage
    from langchain_core.messages.utils import count_tokens_approximately

    from context.utils import estimate_context_tokens

    msgs = [HumanMessage(content="你好")]
    expected = build_system_messages("你是助手", "摘要", now=FIXED_NOW) + msgs
    # estimate_context_tokens 内部用 build_system_messages 构造层级后 count；
    # 显式构造（固定 now）与内部构造（动态 now，定宽格式字符数相同）→ 估算永不偏离注入
    assert estimate_context_tokens(msgs, "你是助手", "摘要") == count_tokens_approximately(
        expected, chars_per_token=1.5)


def test_format_messages_for_summary_extracts_text_from_multimodal():
    """多模态 content 数组 → 摘要只取文本块，图片归一为 [图片]，绝不带 base64。"""
    from langchain_core.messages import HumanMessage

    from context.utils import format_messages_for_summary

    msg = HumanMessage(content=[
        {"type": "text", "text": "看图 "},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAA"}},
    ])
    out = format_messages_for_summary([msg])
    assert "[Human]: 看图 [图片]" in out
    assert "base64" not in out


def test_summary_trim_accepts_callable_counter():
    """回归：trim_messages 新版不接受 chars_per_token 关键字，须走 callable 计数器。"""
    from langchain_core.messages import HumanMessage
    from langchain_core.messages.utils import count_tokens_approximately, trim_messages

    from orchestration.nodes.action_node.summarize import _approx_token_counter

    msgs = [HumanMessage(content="hello world hello world")]
    # 计数器与 estimate_context_tokens 的 1.5 字符/token 语义一致
    assert _approx_token_counter(msgs) == count_tokens_approximately(msgs, chars_per_token=1.5)
    trimmed = trim_messages(
        msgs, max_tokens=1000, token_counter=_approx_token_counter, strategy="last",
    )
    assert len(trimmed) == 1


"""技能层：索引 + 激活正文注入；估算与实际注入一致。"""

from skill import Skill, SkillRegistry


def test_skill_index_and_active_layers_injected():
    registry = SkillRegistry({
        "translate": Skill(name="translate", description="中英互译", body="## 规则\n保留语气"),
        "weather": Skill(name="weather", description="播报天气", body="## 规则\n查天气"),
    })
    msgs = build_system_messages(
        "你是助手", "摘要", now=FIXED_NOW,
        skill_registry=registry, active_skills=["translate"],
    )
    assert [m.content for m in msgs] == [
        "你是助手", TIME_HINT, "之前的对话摘要：\n摘要",
        "可用技能（按需用 load_skill 加载正文）：\n- translate: 中英互译\n- weather: 播报天气",
        "当前已激活技能（遵循其规则）：\n\n===== 技能：translate =====\n## 规则\n保留语气",
    ]


def test_skill_layers_skipped_when_no_registry():
    msgs = build_system_messages("你是助手", "摘要", now=FIXED_NOW)
    assert [m.content for m in msgs] == ["你是助手", TIME_HINT, "之前的对话摘要：\n摘要"]


def test_skill_index_truncated_when_exceeds_max():
    registry = SkillRegistry(
        {f"s{i}": Skill(name=f"s{i}", description=f"d{i}", body="b") for i in range(5)},
        index_max=3,
    )
    msgs = build_system_messages("你是助手", "", now=FIXED_NOW, skill_registry=registry)
    index_msg = [m for m in msgs if "可用技能" in m.content]
    assert index_msg and "…共 5 个技能，仅显示前 3 个" in index_msg[0].content


def test_active_skill_missing_body_skipped_keeps_others():
    registry = SkillRegistry({"a": Skill(name="a", description="d", body="正文A")})
    msgs = build_system_messages(
        "你是助手", "", now=FIXED_NOW,
        skill_registry=registry, active_skills=["a", "ghost"],
    )
    active_msg = [m for m in msgs if "已激活技能" in m.content]
    assert len(active_msg) == 1
    assert "ghost" not in active_msg[0].content
    assert "正文A" in active_msg[0].content


def test_no_active_layer_when_empty_active_skills():
    registry = SkillRegistry({"a": Skill(name="a", description="d", body="正文A")})
    msgs = build_system_messages("你是助手", "", now=FIXED_NOW, skill_registry=registry, active_skills=[])
    assert not any("已激活技能" in m.content for m in msgs)


def test_estimate_includes_skill_layers():
    from langchain_core.messages import HumanMessage
    from langchain_core.messages.utils import count_tokens_approximately

    from context.utils import estimate_context_tokens

    registry = SkillRegistry({"translate": Skill(name="translate", description="中英互译", body="## 规则")})
    msgs = [HumanMessage(content="你好")]
    expected = build_system_messages(
        "你是助手", "摘要", now=FIXED_NOW, skill_registry=registry, active_skills=["translate"],
    ) + msgs
    assert estimate_context_tokens(
        msgs, "你是助手", "摘要", skill_registry=registry, active_skills=["translate"],
    ) == count_tokens_approximately(expected, chars_per_token=1.5)
