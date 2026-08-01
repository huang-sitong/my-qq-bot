"""测试桩：脚本化 LLM、Stub RAG 服务、最小图状态工厂。

ScriptedLLM.bind_tools 返回 self —— ainvoke 忽略工具 schema、按序弹出
脚本消息，因此工具绑定路径与普通路径共用同一条消息队列。
"""

from langchain_core.messages import AIMessage


class ScriptedLLM:
    """按序返回脚本响应的假 LLM。"""

    def __init__(self, responses: list[AIMessage]):
        self._responses = list(responses)
        self._index = 0
        self.last_messages = None

    def bind_tools(self, tools, **kwargs):
        return self

    async def ainvoke(self, messages, **kwargs):
        if self._index >= len(self._responses):
            raise AssertionError("ScriptedLLM exhausted: no more scripted responses")
        self.last_messages = list(messages)
        msg = self._responses[self._index]
        self._index += 1
        return msg


class StubRagService:
    """假 RagService：enabled 开关 + 脚本化检索结果。"""

    def __init__(self, enabled=True, search_results=None, raise_on_search=False):
        self.enabled = enabled
        self.search_results = search_results or []
        self.raise_on_search = raise_on_search
        self.last_query = None
        self.last_thread_id = None
        self.last_indexed = None

    async def search(self, query, thread_id, top_k=None, score_threshold=None):
        self.last_query = query
        self.last_thread_id = thread_id
        if self.raise_on_search:
            raise RuntimeError("search failed")
        return self.search_results

    async def index_turn(self, thread_id, user_id, user_name, user_message, bot_reply):
        self.last_indexed = {
            "thread_id": thread_id,
            "user_id": user_id,
            "user_name": user_name,
            "user_message": user_message,
            "bot_reply": bot_reply,
        }


def make_state(**overrides) -> dict:
    """构造最小图状态，供节点单元测试使用。"""
    state = {
        "messages": [],
        "persona": "你是{bot_name}",
        "conversation_summary": "",
        "session_id": "test:session",
        "thread_id": "test:thread",
        "user_id": "u1",
        "new_message": None,
        "reply_text": "",
        "should_respond": True,
        "bot_name": "测试机器人",
        "channel_type": 0,
        "bot_id": "bot1",
        "raw_content": "你好",
        "user_name": "张三",
        "tool_rounds": 0,
        "content_kind": "text",
    }
    state.update(overrides)
    return state


class StubMemoryStore:
    """内存版 MemoryStore，供记忆工具测试。"""

    def __init__(self):
        self._data: dict[tuple[str, str], str] = {}

    def store_memory(self, user_id: str, key: str, value: str) -> None:
        self._data[(user_id, key)] = value

    def load_memories(self, user_id: str) -> list[dict]:
        return [
            {"key": k, "value": v}
            for (uid, k), v in self._data.items()
            if uid == user_id
        ]


class FakeVisionService:
    """脚本化描述序列的假视觉服务。"""

    def __init__(self, descriptions=None):
        self.descriptions = descriptions or []
        self.calls = 0
        self.last_srcs = None

    async def describe_many(self, srcs):
        self.calls += 1
        self.last_srcs = srcs
        return list(self.descriptions)
