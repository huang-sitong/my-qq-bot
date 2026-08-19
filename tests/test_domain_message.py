from bot.package.conversation import IncomingMessage
from bot.package.domain import IndexTurnTask


def test_incoming_message_is_immutable_domain_event():
    msg = IncomingMessage(
        event_id="llonebot:1:m1",
        platform="llonebot",
        guild_id="g1",
        thread_id="llonebot:g1:c1",
        channel_id="c1",
        channel_type=0,
        user_id="u1",
        user_name="张三",
        raw_content="<img src=\"https://x/1.jpg\"/>",
        content_kind="image",
        has_text=False,
        llm_text="[图片]",
        clean_text="",
        mentions={},
        image_srcs=["https://x/1.jpg"],
        event_type="message-created",
        trace_id="trace-1",
    )
    assert msg.thread_id == "llonebot:g1:c1"
    assert msg.image_srcs == ["https://x/1.jpg"]
    assert msg.event_type == "message-created"
    assert msg.trace_id == "trace-1"


def test_index_turn_task_is_immutable():
    task = IndexTurnTask(
        thread_id="t1",
        user_id="u1",
        user_name="张三",
        bot_id="bot1",
        bot_name="小助手",
        user_message="你好",
        bot_reply="收到",
    )
    assert task.bot_reply == "收到"
    assert task.trace_id == ""


def test_index_turn_task_carries_trace_id():
    task = IndexTurnTask(
        thread_id="t1",
        user_id="u1",
        user_name="张三",
        bot_id="bot1",
        bot_name="小助手",
        user_message="你好",
        bot_reply="收到",
        trace_id="trace-123",
    )
    assert task.trace_id == "trace-123"
