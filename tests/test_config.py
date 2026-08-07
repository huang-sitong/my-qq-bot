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
