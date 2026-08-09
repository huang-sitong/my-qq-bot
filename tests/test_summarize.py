"""summarize_node：多模态 content 列表摘要归一化为纯文本。"""

import asyncio

from langchain_core.messages import AIMessage, HumanMessage

from bot.core.nodes.action_node.summarize import summarize_node
from common import BotConfig
from tests.fakes import ScriptedLLM, make_state


def test_summary_from_multimodal_list_content_normalized_to_text():
    """回归：摘要 LLM 返回 content 块列表时，conversation_summary 归一化为纯文本字符串。"""
    llm = ScriptedLLM([AIMessage(content=[{"type": "text", "text": "聊过猫和狗"}])])
    # 小上下文窗口让 summarize 触发（trigger = 0.5 * 100 = 50 tokens）
    config = BotConfig(
        llm_context_window=100,
        summary_trigger_ratio=0.5,
        summary_keep_ratio=0.1,
    )
    state = make_state(messages=[
        HumanMessage(content="你好，这是我的第一条较长的消息，包含一些关于猫的背景信息，请记住"),
        AIMessage(content="好的，我记住了。你提到你有一只猫，叫咪咪，喜欢吃鱼。"),
        HumanMessage(content="然后我们还聊过狗，你喜欢金毛犬，打算明年养一只。"),
        AIMessage(content="明白了，你明年想养一只金毛犬。"),
    ])
    result = asyncio.run(summarize_node(state, llm=llm, bot_config=config))
    assert result["conversation_summary"] == "聊过猫和狗"
    assert isinstance(result["conversation_summary"], str)
