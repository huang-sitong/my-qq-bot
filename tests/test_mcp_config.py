"""BotConfig MCP 配置字段 + build_mcp_connections 测试。"""

from bot.core.mcp import build_mcp_connections
from common import BotConfig
from common.mcp import parse_mcp_servers


def _clear_config_env(monkeypatch) -> None:
    for field in BotConfig.model_fields.values():
        if isinstance(field.validation_alias, str):
            monkeypatch.delenv(field.validation_alias, raising=False)


def test_mcp_disabled_by_default(monkeypatch):
    _clear_config_env(monkeypatch)
    cfg = BotConfig(_env_file=None)
    assert cfg.mcp_enabled is False


def test_mcp_enabled_flag(monkeypatch):
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("BOT_MCP_ENABLED", "1")
    cfg = BotConfig(_env_file=None)
    assert cfg.mcp_enabled is True


def test_mcp_tool_name_prefix_false_by_default(monkeypatch):
    _clear_config_env(monkeypatch)
    cfg = BotConfig(_env_file=None)
    assert cfg.mcp_tool_name_prefix is False


def test_mcp_tool_name_prefix_flag(monkeypatch):
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("BOT_MCP_TOOL_NAME_PREFIX", "1")
    cfg = BotConfig(_env_file=None)
    assert cfg.mcp_tool_name_prefix is True


def test_tavily_connection_auto_registered_when_key_set():
    servers = build_mcp_connections({}, "sk-test")
    assert "tavily" in servers
    assert servers["tavily"]["transport"] == "streamable_http"
    assert servers["tavily"]["url"].startswith("https://mcp.tavily.com/mcp/?")
    assert "sk-test" in servers["tavily"]["url"]


def test_no_tavily_without_key():
    servers = build_mcp_connections({}, "  ")
    assert "tavily" not in servers


def test_parse_mcp_servers_accepts_dict():
    assert parse_mcp_servers({"x": {"transport": "stdio"}}) == {"x": {"transport": "stdio"}}


def test_parse_mcp_servers_parses_json():
    assert parse_mcp_servers('{"x": {"transport": "stdio"}}') == {
        "x": {"transport": "stdio"},
    }


def test_parse_mcp_servers_empty_json_degrades():
    assert parse_mcp_servers("") == {}
    assert parse_mcp_servers("{}") == {}


def test_extra_servers_from_env_json(monkeypatch):
    _clear_config_env(monkeypatch)
    monkeypatch.setenv(
        "BOT_MCP_SERVERS",
        '{"weather": {"transport": "streamable_http", "url": "http://localhost:8000/mcp"}}',
    )
    cfg = BotConfig(_env_file=None)
    assert cfg.mcp_servers["weather"]["url"] == "http://localhost:8000/mcp"


def test_invalid_mcp_servers_json_degrades(monkeypatch):
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("BOT_MCP_SERVERS", "{not json")
    cfg = BotConfig(_env_file=None)
    assert cfg.mcp_servers == {}


def test_extra_servers_merge_with_tavily():
    servers = build_mcp_connections(
        {"weather": {"transport": "streamable_http", "url": "http://localhost:8000/mcp"}},
        "sk-test",
    )
    assert set(servers) == {"weather", "tavily"}
    assert "sk-test" in servers["tavily"]["url"]
