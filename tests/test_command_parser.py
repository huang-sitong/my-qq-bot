"""命令解析测试：prefix、大小写、位置参数、引号与非法输入。"""

from bot.core.commands.parser import ParsedCommand, parse_command


def test_recognizes_default_prefix():
    assert parse_command("/ping", "/") == ParsedCommand(name="ping")


def test_command_name_is_lowercased():
    assert parse_command("/HELP status", "/") == ParsedCommand(
        name="help", args=("status",)
    )


def test_parses_quoted_arguments():
    assert parse_command('/skill "my skill"', "/") == ParsedCommand(
        name="skill", args=("my skill",)
    )


def test_ignores_message_without_prefix():
    assert parse_command("你好 /ping", "/") is None


def test_empty_command_returns_none():
    assert parse_command("/", "/") is None


def test_invalid_command_name_returns_none():
    assert parse_command("/Bad.Name", "/") is None


def test_unterminated_quote_reports_error():
    parsed = parse_command('/help "oops', "/")
    assert parsed is not None
    assert parsed.name == "help"
    assert parsed.error


def test_custom_prefix():
    assert parse_command("!ping", "!") == ParsedCommand(name="ping")
