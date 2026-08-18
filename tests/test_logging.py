"""日志辅助函数测试：上下文 Message 的格式化与统一打印。"""

import logging

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from context.utils import format_message_for_log, log_context_message


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
