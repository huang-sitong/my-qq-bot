# tests/test_skill_loader.py
"""SkillRegistry 加载器测试：frontmatter 解析、非法跳过、索引截断。"""

from bot.core.skills import Skill, SkillRegistry


def _write_skill(tmp_path, name, md_text):
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(md_text, encoding="utf-8")
    return d


def test_parses_frontmatter_and_body(tmp_path):
    _write_skill(tmp_path, "translate", (
        "---\n"
        "name: translate\n"
        "description: 中英互译\n"
        "---\n"
        "\n"
        "## 规则\n"
        "保留语气"
    ))
    reg = SkillRegistry.from_directory(str(tmp_path))
    assert reg.names() == ["translate"]
    assert reg.total == 1
    assert reg.has("translate")
    assert "保留语气" in reg.get_body("translate")


def test_missing_directory_returns_empty(tmp_path):
    reg = SkillRegistry.from_directory(str(tmp_path / "nope"))
    assert reg.total == 0
    assert reg.index_text() == ""


def test_skips_skill_without_frontmatter(tmp_path):
    _write_skill(tmp_path, "bad", "## 没有 frontmatter 的正文")
    reg = SkillRegistry.from_directory(str(tmp_path))
    assert reg.names() == []


def test_skips_skill_missing_name_or_description(tmp_path):
    _write_skill(tmp_path, "a", "---\ndescription: 缺 name\n---\n正文")
    _write_skill(tmp_path, "b", "---\nname: b\n---\n正文")
    reg = SkillRegistry.from_directory(str(tmp_path))
    assert reg.names() == []


def test_skips_invalid_name(tmp_path):
    _write_skill(tmp_path, "Bad Name", "---\nname: Bad Name\ndescription: 含空格\n---\n正文")
    reg = SkillRegistry.from_directory(str(tmp_path))
    assert reg.names() == []


def test_duplicate_name_last_wins(tmp_path):
    _write_skill(tmp_path, "a", "---\nname: dup\ndescription: 第一版\n---\nbody1")
    _write_skill(tmp_path, "b", "---\nname: dup\ndescription: 第二版\n---\nbody2")
    reg = SkillRegistry.from_directory(str(tmp_path))
    assert reg.names() == ["dup"]
    assert reg.get_body("dup") == "body2"


def test_index_text_truncates_and_notes_total(tmp_path):
    for i in range(5):
        _write_skill(tmp_path, f"s{i}", f"---\nname: s{i}\ndescription: d{i}\n---\nb")
    reg = SkillRegistry.from_directory(str(tmp_path), index_max=3)
    text = reg.index_text()
    assert "- s0: d0" in text
    assert "…共 5 个技能，仅显示前 3 个" in text


def test_index_text_all_when_under_max(tmp_path):
    for i in range(2):
        _write_skill(tmp_path, f"s{i}", f"---\nname: s{i}\ndescription: d{i}\n---\nb")
    reg = SkillRegistry.from_directory(str(tmp_path), index_max=3)
    assert "…共" not in reg.index_text()


def test_in_memory_construction_for_tests():
    reg = SkillRegistry({"x": Skill(name="x", description="d", body="b")}, index_max=5)
    assert reg.names() == ["x"]
    assert reg.has("x")
    assert reg.get_body("x") == "b"
    assert reg.get_body("ghost") is None


def test_skips_non_utf8_skill_without_crash(tmp_path):
    """GBK 编码的 SKILL.md 解码失败应跳过并告警，绝不崩 from_directory。"""
    d = tmp_path / "gbk"
    d.mkdir()
    (d / "SKILL.md").write_bytes(
        "---\nname: gbk\ndescription: 中文技能\n---\n正文\n".encode("gbk")
    )
    reg = SkillRegistry.from_directory(str(tmp_path))
    assert reg.total == 0
    assert reg.names() == []


def test_parses_frontmatter_without_trailing_newline(tmp_path):
    """关闭 ``---`` 后无尾换行（文件以 ``---`` 结尾）也应解析。"""
    _write_skill(tmp_path, "ok", "---\nname: ok\ndescription: fine\n---")
    reg = SkillRegistry.from_directory(str(tmp_path))
    assert reg.names() == ["ok"]
    assert reg.get_body("ok") == ""


def test_get_skill_returns_skill_or_none():
    skill = Skill(name="x", description="d", body="b")
    reg = SkillRegistry({"x": skill})
    assert reg.get_skill("x") == skill
    assert reg.get_skill("ghost") is None
