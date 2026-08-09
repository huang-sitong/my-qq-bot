"""BotConfig LLM 多模态字段测试。"""

from common import BotConfig


def test_llm_multimodal_disabled_by_default(monkeypatch):
    monkeypatch.delenv("BOT_LLM_MULTIMODAL", raising=False)
    assert BotConfig().llm_multimodal is False


def test_llm_multimodal_enabled(monkeypatch):
    monkeypatch.setenv("BOT_LLM_MULTIMODAL", "1")
    assert BotConfig().llm_multimodal is True


def test_llm_multimodal_false_values(monkeypatch):
    for v in ("0", "false", "False", ""):
        monkeypatch.setenv("BOT_LLM_MULTIMODAL", v)
        assert BotConfig().llm_multimodal is False


def test_skills_enabled_by_default(monkeypatch):
    monkeypatch.delenv("BOT_SKILLS_ENABLED", raising=False)
    assert BotConfig().skills_enabled is True


def test_skills_enabled_false_values(monkeypatch):
    for v in ("0", "false", "False", ""):
        monkeypatch.setenv("BOT_SKILLS_ENABLED", v)
        assert BotConfig().skills_enabled is False


def test_skills_dir_and_index_max_defaults(monkeypatch):
    monkeypatch.delenv("BOT_SKILLS_DIR", raising=False)
    monkeypatch.delenv("BOT_SKILLS_INDEX_MAX", raising=False)
    config = BotConfig()
    assert config.skills_dir == "skills"
    assert config.skills_index_max == 50


def test_skills_dir_and_index_max_env(monkeypatch):
    monkeypatch.setenv("BOT_SKILLS_DIR", "my-skills")
    monkeypatch.setenv("BOT_SKILLS_INDEX_MAX", "120")
    config = BotConfig()
    assert config.skills_dir == "my-skills"
    assert config.skills_index_max == 120
