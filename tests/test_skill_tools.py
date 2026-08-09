# tests/test_skill_tools.py
"""load_skill / unload_skill 纯函数测试。"""

import asyncio

from bot.core.skills import Skill, SkillRegistry, load_skill, unload_skill


def _registry():
    return SkillRegistry({
        "translate": Skill(name="translate", description="中英互译", body="## 规则\n保留语气"),
    })


def test_load_skill_returns_body():
    out = asyncio.run(load_skill("translate", _registry()))
    assert "保留语气" in out
    assert "translate" in out


def test_load_skill_unknown_name_lists_available():
    out = asyncio.run(load_skill("ghost", _registry()))
    assert "ghost" in out
    assert "translate" in out


def test_load_skill_no_registry_returns_disabled():
    out = asyncio.run(load_skill("translate", None))
    assert "未启用" in out


def test_unload_skill_idempotent_confirmation():
    out1 = asyncio.run(unload_skill("translate"))
    out2 = asyncio.run(unload_skill("translate"))
    assert out1 == out2
    assert "translate" in out1


def test_load_skill_appends_data_entry(tmp_path):
    """技能带 data_path 时，load_skill 返回正文后追加一条随机抽取记录。"""
    data = tmp_path / "data.json"
    data.write_text('[{"puzzle": "P", "answer": "A"}]', encoding="utf-8")
    reg = SkillRegistry({
        "soup": Skill(name="soup", description="海龟汤", body="## 规则\n主持", data_path=data),
    })
    out = asyncio.run(load_skill("soup", reg))
    assert "主持" in out
    assert "puzzle: P" in out
    assert "answer: A" in out


def test_load_skill_without_data_has_no_data_section():
    out = asyncio.run(load_skill("translate", _registry()))
    assert "附带数据" not in out
