"""日志辅助函数测试：上下文 Message 的格式化与统一打印。"""

import json
import logging
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from bot.package.utils import format_message_for_log, log_context_message
from bot.package.utils.logging import setup_logging


def test_format_message_for_log_human_with_name():
    message = HumanMessage(content="你好", name="Alice")
    assert format_message_for_log(message) == "[Human|Alice]: 你好"


def test_format_message_for_log_ai_without_name():
    message = AIMessage(content="我在这里")
    assert format_message_for_log(message) == "[AI]: 我在这里"


def test_format_message_for_log_tool():
    message = ToolMessage(content="查询结果", tool_call_id="call_1")
    assert format_message_for_log(message) == "[Tool]: 查询结果"


def test_format_message_for_log_tool_calls():
    message = AIMessage(
        content="",
        tool_calls=[{"id": "call_1", "name": "search_chat_history", "args": {"query": "qq"}}],
    )
    text = format_message_for_log(message)
    assert "tool_calls: search_chat_history" in text
    assert "{'query': 'qq'}" in text


def test_log_context_message_includes_extra_fields(caplog):
    logger = logging.getLogger("test.context")
    message = HumanMessage(content="hello")
    with caplog.at_level(logging.INFO, logger="test.context"):
        log_context_message(
            message,
            logger=logger,
            prefix="Context message",
            thread_id="thread:1",
            trace_id="trace-1",
        )
    assert "Context message thread_id=thread:1 trace_id=trace-1: [Human]: hello" in caplog.text


def _read_web_jsonl(log_dir: Path) -> list[dict]:
    path = log_dir / "web.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").strip().splitlines()
        if line.strip()
    ]


def test_setup_logging_web_jsonl_filters_debug(tmp_path: Path):
    """web.jsonl 应只包含 INFO 及以上日志，不包含 DEBUG。"""
    root = logging.getLogger()
    original_level = root.level
    try:
        log_dir = setup_logging(
            tmp_path,
            level=logging.INFO,
            console_level=logging.INFO,
            file_level=logging.INFO,
        )
        logger = logging.getLogger("test.web")
        logger.debug("a-debug %s", 1)
        logger.info("an-info")
        logger.warning("a-warning")
        logger.error("a-error")

        entries = _read_web_jsonl(log_dir)
        levels = [e["level"] for e in entries]
        assert levels == ["INFO", "WARNING", "ERROR"]
        assert all(e["logger"] == "test.web" for e in entries)
        assert entries[0]["message"] == "an-info"
        assert entries[0]["trace_id"] == "-"
    finally:
        root.setLevel(original_level)


def test_setup_logging_web_jsonl_truncates_on_new_run(tmp_path: Path):
    """再次 setup_logging（模拟 bot 重启）应重建 web.jsonl，只保留本次运行日志。"""
    root = logging.getLogger()
    original_level = root.level
    try:
        log_dir = setup_logging(
            tmp_path,
            level=logging.INFO,
            console_level=logging.INFO,
            file_level=logging.INFO,
        )
        logging.getLogger("test.run1").info("old-run")

        # 模拟 bot 重启：重新初始化日志，web.jsonl 应被清空重建
        setup_logging(
            tmp_path,
            level=logging.INFO,
            console_level=logging.INFO,
            file_level=logging.INFO,
        )
        logging.getLogger("test.run2").info("new-run")

        entries = _read_web_jsonl(log_dir)
        assert [e["message"] for e in entries] == ["new-run"]
    finally:
        root.setLevel(original_level)



def test_setup_logging_terminal_filters_debug(capsys, tmp_path: Path):
    """终端（StreamHandler）级别为 INFO，应过滤 DEBUG。"""
    root = logging.getLogger()
    original_level = root.level
    try:
        setup_logging(
            tmp_path,
            level=logging.DEBUG,
            console_level=logging.INFO,
            file_level=logging.INFO,
        )
        logger = logging.getLogger("test.term")
        logger.debug("should-not-print")
        logger.info("should-print")
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "should-not-print" not in combined
        assert "should-print" in combined
    finally:
        root.setLevel(original_level)
