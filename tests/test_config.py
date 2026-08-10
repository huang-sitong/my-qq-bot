"""BotConfig 默认值、env 映射、校验和模板一致性测试。"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from common import DEFAULT_PERSONA_PROMPT, BotConfig

ROOT = Path(__file__).resolve().parents[1]


EXPECTED_DEFAULTS = {
    "ws_url": "ws://localhost:5600/v1/events",
    "token": None,
    "reconnect": True,
    "max_reconnect_delay": 30,
    "api_base_url": "http://localhost:5600",
    "api_platform": "llonebot",
    "llm_base_url": None,
    "llm_api_key": None,
    "llm_model": "sensenova-6.7-flash-lite",
    "llm_temperature": 0.7,
    "llm_max_retries": 1,
    "llm_request_timeout": 30,
    "llm_multimodal": False,
    "llm_context_window": 200_000,
    "summary_trigger_ratio": 0.8,
    "summary_keep_ratio": 0.2,
    "summary_max_input_tokens": 8_000,
    "db_dir": "db",
    "persona_prompt": DEFAULT_PERSONA_PROMPT,
    "rag_enabled": True,
    "embed_model": "qwen3-embedding:0.6b",
    "ollama_base_url": "http://localhost:11434",
    "embed_dimensions": 1024,
    "embed_cache_enabled": True,
    "embed_cache_max_entries": 20_000,
    "rag_top_k": 5,
    "rag_score_threshold": 0.35,
    "rag_retention_per_thread": 2000,
    "rag_max_agent_rounds": 7,
    "vision_enabled": True,
    "vision_model": "qwen3-vl:2b",
    "vision_max_images": 3,
    "vision_timeout": 60,
    "mcp_enabled": False,
    "mcp_servers": {},
    "mcp_tool_name_prefix": False,
    "tavily_api_key": "",
    "skills_enabled": True,
    "skills_dir": "skills",
    "skills_index_max": 50,
}


ENV_SAMPLES = {
    "ws_url": ("ws://env", "ws://env"),
    "token": ("tok", "tok"),
    "reconnect": ("0", False),
    "max_reconnect_delay": ("15", 15),
    "api_base_url": ("http://env", "http://env"),
    "api_platform": ("env", "env"),
    "llm_base_url": ("https://llm.env", "https://llm.env"),
    "llm_api_key": ("key", "key"),
    "llm_model": ("model", "model"),
    "llm_temperature": ("0.5", 0.5),
    "llm_max_retries": ("2", 2),
    "llm_request_timeout": ("10", 10),
    "llm_multimodal": ("1", True),
    "llm_context_window": ("1000", 1000),
    "summary_trigger_ratio": ("0.9", 0.9),
    "summary_keep_ratio": ("0.3", 0.3),
    "summary_max_input_tokens": ("100", 100),
    "db_dir": ("db-env", "db-env"),
    "persona_prompt": ("prompt", "prompt"),
    "rag_enabled": ("0", False),
    "embed_model": ("model", "model"),
    "ollama_base_url": ("http://ollama", "http://ollama"),
    "embed_dimensions": ("8", 8),
    "embed_cache_enabled": ("0", False),
    "embed_cache_max_entries": ("10", 10),
    "rag_top_k": ("3", 3),
    "rag_score_threshold": ("0.4", 0.4),
    "rag_retention_per_thread": ("100", 100),
    "rag_max_agent_rounds": ("2", 2),
    "vision_enabled": ("0", False),
    "vision_model": ("model", "model"),
    "vision_max_images": ("2", 2),
    "vision_timeout": ("10", 10),
    "mcp_enabled": ("1", True),
    "mcp_servers": (
        '{"mcp": {"transport": "stdio", "command": "x"}}',
        {"mcp": {"transport": "stdio", "command": "x"}},
    ),
    "mcp_tool_name_prefix": ("1", True),
    "tavily_api_key": ("tvly", "tvly"),
    "skills_enabled": ("0", False),
    "skills_dir": ("skills-env", "skills-env"),
    "skills_index_max": ("10", 10),
}


def _env_aliases() -> dict[str, str]:
    aliases = {}
    for field_name, field in BotConfig.model_fields.items():
        assert isinstance(field.validation_alias, str)
        aliases[field_name] = field.validation_alias
    return aliases


def _clear_config_env(monkeypatch) -> None:
    for alias in _env_aliases().values():
        monkeypatch.delenv(alias, raising=False)


def test_defaults_without_env_file(monkeypatch):
    # 第三方库导入可能把 .env 注入 os.environ，测试默认值前先清理。
    _clear_config_env(monkeypatch)
    config = BotConfig(_env_file=None)
    assert {name: getattr(config, name) for name in EXPECTED_DEFAULTS} == EXPECTED_DEFAULTS


def test_every_field_has_env_alias():
    assert set(_env_aliases()) == set(EXPECTED_DEFAULTS)


def test_every_env_alias_maps_to_field(monkeypatch):
    aliases = _env_aliases()
    for field_name, (env_value, expected) in ENV_SAMPLES.items():
        for alias in aliases.values():
            monkeypatch.delenv(alias, raising=False)
        monkeypatch.setenv(aliases[field_name], env_value)
        config = BotConfig(_env_file=None)
        assert getattr(config, field_name) == expected


def test_invalid_bool_rejected(monkeypatch):
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("BOT_RECONNECT", "2")
    with pytest.raises(ValidationError):
        BotConfig(_env_file=None)


def test_invalid_number_rejected(monkeypatch):
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("BOT_LLM_TEMPERATURE", "abc")
    with pytest.raises(ValidationError):
        BotConfig(_env_file=None)


def test_out_of_range_ratio_rejected(monkeypatch):
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("BOT_RAG_SCORE_THRESHOLD", "2")
    with pytest.raises(ValidationError):
        BotConfig(_env_file=None)


def test_summary_keep_ratio_must_not_exceed_trigger(monkeypatch):
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("BOT_SUMMARY_TRIGGER_RATIO", "0.5")
    monkeypatch.setenv("BOT_SUMMARY_KEEP_RATIO", "0.9")
    with pytest.raises(ValidationError):
        BotConfig(_env_file=None)


def test_env_template_contains_all_env_aliases():
    template = (ROOT / ".env-template").read_text(encoding="utf-8")
    missing = [
        alias
        for alias in sorted(_env_aliases().values())
        if alias not in template
    ]
    assert missing == []


def test_production_code_does_not_read_env_directly():
    source_files = [
        ROOT / "main.py",
        *sorted((ROOT / "common").rglob("*.py")),
        *sorted((ROOT / "bot").rglob("*.py")),
    ]
    for path in source_files:
        text = path.read_text(encoding="utf-8")
        assert "os.getenv" not in text, path
        assert "os.environ" not in text, path
