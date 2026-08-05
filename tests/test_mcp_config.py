"""BotConfig MCP 配置字段测试。"""

from common import BotConfig


def test_mcp_disabled_by_default(monkeypatch):
    monkeypatch.delenv("BOT_MCP_ENABLED", raising=False)
    cfg = BotConfig()
    assert cfg.mcp_enabled is False


def test_mcp_enabled_flag(monkeypatch):
    monkeypatch.setenv("BOT_MCP_ENABLED", "1")
    cfg = BotConfig()
    assert cfg.mcp_enabled is True


def test_tavily_connection_auto_registered_when_key_set():
    cfg = BotConfig(tavily_api_key="sk-test")
    servers = cfg.mcp_server_connections()
    assert "tavily" in servers
    assert servers["tavily"]["transport"] == "streamable_http"
    assert servers["tavily"]["url"].startswith("https://mcp.tavily.com/mcp/?")
    assert "sk-test" in servers["tavily"]["url"]


def test_no_tavily_without_key():
    cfg = BotConfig(tavily_api_key="  ")
    servers = cfg.mcp_server_connections()
    assert "tavily" not in servers


def test_extra_servers_from_env_json(monkeypatch):
    monkeypatch.setenv(
        "BOT_MCP_SERVERS",
        '{"weather": {"transport": "streamable_http", "url": "http://localhost:8000/mcp"}}',
    )
    cfg = BotConfig()
    assert cfg.mcp_servers["weather"]["url"] == "http://localhost:8000/mcp"


def test_invalid_mcp_servers_json_degrades(monkeypatch):
    monkeypatch.setenv("BOT_MCP_SERVERS", "{not json")
    cfg = BotConfig()
    assert cfg.mcp_servers == {}


def test_extra_servers_merge_with_tavily():
    cfg = BotConfig(
        mcp_servers={
            "weather": {"transport": "streamable_http", "url": "http://localhost:8000/mcp"},
        },
        tavily_api_key="sk-test",
    )
    servers = cfg.mcp_server_connections()
    assert set(servers) == {"weather", "tavily"}
