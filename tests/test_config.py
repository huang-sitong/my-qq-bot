"""BotConfig 默认值、env 映射、校验和模板一致性测试。"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from bot.package.config import BotConfig
from bot.package.config.settings import DEFAULT_PERSONA_PROMPT

ROOT = Path(__file__).resolve().parents[1]


EXPECTED_DEFAULTS = {
    "ws_url": "ws://localhost:5600/v1/events",
    "token": None,
    "reconnect": True,
    "max_reconnect_delay": 30,
    "api_base_url": "http://localhost:5600",
    "onebot11_api_base_url": "http://localhost:3000",
    "onebot11_timeout": 60,
    "api_platform": "llonebot",
    "llm_base_url": None,
    "llm_api_key": None,
    "llm_model": "deepseek-v4-flash",
    "llm_temperature": 0.7,
    "llm_max_retries": 1,
    "llm_request_timeout": 30,
    "llm_multimodal": False,
    "llm_context_window": 500_000,
    "summary_trigger_ratio": 0.8,
    "summary_keep_ratio": 0.2,
    "summary_max_input_tokens": 8_000,
    "db_dir": "db",
    "persona_prompt": DEFAULT_PERSONA_PROMPT,
    "rag_enabled": True,
    "embed_model": "qwen3-embedding:0.6b",
    "embed_base_url": None,
    "embed_api_key": None,
    "vision_base_url": None,
    "vision_api_key": None,
    "embed_dimensions": 1024,
    "embed_cache_enabled": True,
    "embed_cache_max_entries": 20_000,
    "rag_top_k": 5,
    "rag_score_threshold": 0.35,
    "rag_retention_per_thread": 2000,
    "rag_max_agent_rounds": 12,
    "document_collection": "documents",
    "document_mineru_endpoint": None,
    "document_mineru_api_key": None,
    "document_mineru_agent_enabled": True,
    "document_mineru_timeout": 300,
    "document_chunk_size": 500,
    "document_chunk_overlap": 50,
    "vision_enabled": True,
    "vision_model": "qwen3-vl:2b",
    "vision_max_images": 3,
    "vision_timeout": 60,
    "mcp_enabled": False,
    "mcp_servers_file": "config/mcp_servers.json",
    "mcp_tool_name_prefix": False,
    "skills_enabled": True,
    "skills_dir": "skills",
    "skills_index_max": 50,
    "command_enabled": True,
    "command_prefix": "/",
    "admin_ids": [],
    "auto_reply": False,
    "auto_reply_random_rate": 0.3,
    "auto_reply_cooldown": 30,
    "bash_enabled": True,
    "bash_shell": "bash",
    "bash_timeout": 30,
    "bash_max_output": 4000,
    "bash_allowed_roots": [],
    "message_worker_count": 1,
    "message_queue_maxsize": 0,
    "message_batch_max": 4,
    "message_dedup_size": 10000,
    "graph_recursion_limit": 128,
}


ENV_SAMPLES = {
    "ws_url": ("ws://env", "ws://env"),
    "token": ("tok", "tok"),
    "reconnect": ("0", False),
    "max_reconnect_delay": ("15", 15),
    "api_base_url": ("http://env", "http://env"),
    "onebot11_api_base_url": ("http://onebot.env", "http://onebot.env"),
    "onebot11_timeout": ("42", 42),
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
    "embed_base_url": ("http://embed", "http://embed"),
    "embed_api_key": ("ekey", "ekey"),
    "vision_base_url": ("http://vision", "http://vision"),
    "embed_dimensions": ("8", 8),
    "embed_cache_enabled": ("0", False),
    "embed_cache_max_entries": ("10", 10),
    "rag_top_k": ("3", 3),
    "rag_score_threshold": ("0.4", 0.4),
    "rag_retention_per_thread": ("100", 100),
    "rag_max_agent_rounds": ("2", 2),
    "document_collection": ("docs", "docs"),
    "document_mineru_endpoint": ("http://mineru:8000", "http://mineru:8000"),
    "document_mineru_api_key": ("sk-mineru", "sk-mineru"),
    "document_mineru_agent_enabled": ("0", False),
    "document_mineru_timeout": ("120", 120),
    "document_chunk_size": ("800", 800),
    "document_chunk_overlap": ("80", 80),
    "vision_enabled": ("0", False),
    "vision_model": ("model", "model"),
    "vision_api_key": ("vkey", "vkey"),
    "vision_max_images": ("2", 2),
    "vision_timeout": ("10", 10),
    "mcp_enabled": ("1", True),
    "mcp_servers_file": ("mcp.json", "mcp.json"),
    "mcp_tool_name_prefix": ("1", True),
    "skills_enabled": ("0", False),
    "skills_dir": ("skills-env", "skills-env"),
    "skills_index_max": ("10", 10),
    "command_enabled": ("0", False),
    "command_prefix": ("!", "!"),
    "admin_ids": ("u1, u2", ["u1", "u2"]),
    "auto_reply": ("1", True),
    "auto_reply_random_rate": ("0.5", 0.5),
    "auto_reply_cooldown": ("10", 10),
    "bash_enabled": ("0", False),
    "bash_shell": ("bash.exe", "bash.exe"),
    "bash_timeout": ("10", 10),
    "bash_max_output": ("100", 100),
    "bash_allowed_roots": ("C:/work, D:/tmp", ["C:/work", "D:/tmp"]),
    "message_worker_count": ("4", 4),
    "message_queue_maxsize": ("512", 512),
    "message_batch_max": ("8", 8),
    "message_dedup_size": ("200", 200),
    "graph_recursion_limit": ("64", 64),
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


def test_invalid_auto_reply_rejected(monkeypatch):
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("BOT_AUTO_REPLY", "2")
    with pytest.raises(ValidationError):
        BotConfig(_env_file=None)


def test_invalid_auto_reply_random_rate_rejected(monkeypatch):
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("BOT_AUTO_REPLY_RANDOM_RATE", "1.1")
    with pytest.raises(ValidationError):
        BotConfig(_env_file=None)


def test_invalid_auto_reply_cooldown_rejected(monkeypatch):
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("BOT_AUTO_REPLY_COOLDOWN", "-1")
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


def test_empty_command_prefix_rejected(monkeypatch):
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("BOT_COMMAND_PREFIX", "")
    with pytest.raises(ValidationError):
        BotConfig(_env_file=None)


def test_admin_ids_deduplicated_and_stripped(monkeypatch):
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("BOT_ADMIN_IDS", "u1, u1 ,  u2")
    config = BotConfig(_env_file=None)
    assert config.admin_ids == ["u1", "u2"]


def test_bash_allowed_roots_stripped_and_deduped(monkeypatch):
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("BOT_BASH_ALLOWED_ROOTS", "C:/a, C:/a , D:/b")
    config = BotConfig(_env_file=None)
    assert config.bash_allowed_roots == ["C:/a", "D:/b"]


def test_embed_and_vision_urls_use_specific_env(monkeypatch):
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("BOT_EMBED_BASE_URL", "http://embed.local")
    monkeypatch.setenv("BOT_EMBED_API_KEY", "sk-embed")
    monkeypatch.setenv("BOT_VISION_BASE_URL", "http://vision.local")
    monkeypatch.setenv("BOT_VISION_API_KEY", "sk-vision")
    config = BotConfig(_env_file=None)
    assert config.embed_base_url == "http://embed.local"
    assert config.embed_api_key == "sk-embed"
    assert config.vision_base_url == "http://vision.local"
    assert config.vision_api_key == "sk-vision"


def test_embed_url_and_key_fall_back_to_main_llm(monkeypatch):
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("BASE_URL", "https://llm.example.com/v1")
    monkeypatch.setenv("API_KEY", "sk-main")
    config = BotConfig(_env_file=None)
    assert config.embed_base_url == "https://llm.example.com/v1"
    assert config.embed_api_key == "sk-main"


def test_embed_explicit_url_and_key_win_over_fallback(monkeypatch):
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("BASE_URL", "https://llm.example.com/v1")
    monkeypatch.setenv("API_KEY", "sk-main")
    monkeypatch.setenv("BOT_EMBED_BASE_URL", "https://embed.example.com/v1")
    monkeypatch.setenv("BOT_EMBED_API_KEY", "sk-embed")
    config = BotConfig(_env_file=None)
    assert config.embed_base_url == "https://embed.example.com/v1"
    assert config.embed_api_key == "sk-embed"


def test_vision_url_and_key_fall_back_to_main_llm(monkeypatch):
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("BASE_URL", "https://llm.example.com/v1")
    monkeypatch.setenv("API_KEY", "sk-main")
    config = BotConfig(_env_file=None)
    assert config.vision_base_url == "https://llm.example.com/v1"
    assert config.vision_api_key == "sk-main"


def test_vision_explicit_url_and_key_win_over_fallback(monkeypatch):
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("BASE_URL", "https://llm.example.com/v1")
    monkeypatch.setenv("API_KEY", "sk-main")
    monkeypatch.setenv("BOT_VISION_BASE_URL", "https://vision.example.com/v1")
    monkeypatch.setenv("BOT_VISION_API_KEY", "sk-vision")
    config = BotConfig(_env_file=None)
    assert config.vision_base_url == "https://vision.example.com/v1"
    assert config.vision_api_key == "sk-vision"


def test_embed_config_does_not_leak_to_vision(monkeypatch):
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("BOT_EMBED_BASE_URL", "http://embed.local")
    monkeypatch.setenv("BOT_EMBED_API_KEY", "sk-embed")
    config = BotConfig(_env_file=None)
    assert config.embed_base_url == "http://embed.local"
    assert config.embed_api_key == "sk-embed"
    assert config.vision_base_url is None
    assert config.vision_api_key is None


def test_invalid_bash_timeout_rejected(monkeypatch):
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("BOT_BASH_TIMEOUT", "0")
    with pytest.raises(ValidationError):
        BotConfig(_env_file=None)


def test_invalid_onebot11_timeout_rejected(monkeypatch):
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("BOT_ONEBOT11_TIMEOUT", "0")
    with pytest.raises(ValidationError):
        BotConfig(_env_file=None)


def test_invalid_message_worker_count_rejected(monkeypatch):
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("BOT_MESSAGE_WORKER_COUNT", "0")
    with pytest.raises(ValidationError):
        BotConfig(_env_file=None)


def test_invalid_message_queue_maxsize_rejected(monkeypatch):
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("BOT_MESSAGE_QUEUE_MAXSIZE", "-1")
    with pytest.raises(ValidationError):
        BotConfig(_env_file=None)


def test_invalid_message_batch_max_rejected(monkeypatch):
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("BOT_MESSAGE_BATCH_MAX", "-1")
    with pytest.raises(ValidationError):
        BotConfig(_env_file=None)


def test_invalid_message_dedup_size_rejected(monkeypatch):
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("BOT_MESSAGE_DEDUP_SIZE", "-1")
    with pytest.raises(ValidationError):
        BotConfig(_env_file=None)


def test_invalid_graph_recursion_limit_rejected(monkeypatch):
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("BOT_GRAPH_RECURSION_LIMIT", "0")
    with pytest.raises(ValidationError):
        BotConfig(_env_file=None)


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


def test_bash_tool_hint_exported_from_common():
    from bot.package.orchestration.prompts import BASH_TOOL_HINT
    assert "run_bash" in BASH_TOOL_HINT
