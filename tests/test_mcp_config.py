"""BotConfig MCP 配置字段 + load_mcp_servers_from_file 测试。

server 定义集中在 config/mcp_servers.json（可提交），密钥 ${ENV_VAR} 插值。
"""

from bot.package.config import BotConfig
from bot.package.mcp.config import load_mcp_servers_from_file


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


def test_mcp_servers_file_default(monkeypatch):
    _clear_config_env(monkeypatch)
    cfg = BotConfig(_env_file=None)
    assert cfg.mcp_servers_file == "config/mcp_servers.json"


def test_mcp_servers_file_from_env(monkeypatch):
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("BOT_MCP_SERVERS_FILE", "mcp.json")
    cfg = BotConfig(_env_file=None)
    assert cfg.mcp_servers_file == "mcp.json"


# --- load_mcp_servers_from_file ---


def test_loads_committed_file_and_interpolates():
    """相对路径按项目根解析；读提交的 config/mcp_servers.json，${TAVILY_API_KEY} 插值。"""
    servers = load_mcp_servers_from_file(
        "config/mcp_servers.json", env={"TAVILY_API_KEY": "sk-test"}
    )
    assert set(servers) == {"tavily"}
    assert servers["tavily"]["transport"] == "streamable_http"
    assert "sk-test" in servers["tavily"]["url"]


def test_custom_env_mapping(tmp_path):
    f = tmp_path / "s.json"
    f.write_text('{"servers": {"x": {"transport": "stdio", "url": "http://h/${TOK}"}}}')
    servers = load_mcp_servers_from_file(str(f), env={"TOK": "v"})
    assert servers["x"]["url"] == "http://h/v"


def test_interpolates_nested_values(tmp_path):
    f = tmp_path / "s.json"
    f.write_text('{"servers": {"x": {"headers": {"Authorization": "Bearer ${TOK}"}}}}')
    servers = load_mcp_servers_from_file(str(f), env={"TOK": "abc"})
    assert servers["x"]["headers"]["Authorization"] == "Bearer abc"


def test_missing_env_var_interpolates_empty(tmp_path):
    f = tmp_path / "s.json"
    f.write_text('{"servers": {"x": {"url": "http://h/${NOPE}"}}}')
    assert load_mcp_servers_from_file(str(f), env={})["x"]["url"] == "http://h/"


def test_accepts_wrapper_and_bare(tmp_path):
    w = tmp_path / "w.json"
    w.write_text('{"servers": {"a": {"transport": "stdio"}}}')
    b = tmp_path / "b.json"
    b.write_text('{"a": {"transport": "stdio"}}')
    assert load_mcp_servers_from_file(str(w), env={}) == {"a": {"transport": "stdio"}}
    assert load_mcp_servers_from_file(str(b), env={}) == {"a": {"transport": "stdio"}}


def test_missing_file_and_empty_path_degrades(tmp_path):
    assert load_mcp_servers_from_file(str(tmp_path / "nope.json"), env={}) == {}
    assert load_mcp_servers_from_file("", env={}) == {}


def test_corrupt_file_degrades(tmp_path):
    f = tmp_path / "s.json"
    f.write_text("{broken")
    assert load_mcp_servers_from_file(str(f), env={}) == {}


def test_non_object_file_degrades(tmp_path):
    f = tmp_path / "s.json"
    f.write_text("[1, 2]")
    assert load_mcp_servers_from_file(str(f), env={}) == {}
    f2 = tmp_path / "s2.json"
    f2.write_text('{"servers": [1]}')
    assert load_mcp_servers_from_file(str(f2), env={}) == {}
