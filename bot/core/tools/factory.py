"""工具统一层：把内部纯函数 + MCP 工具归一为 BaseTool 列表。

- 内部工具（RAG 检索、用户记忆）用 StructuredTool.from_function 包装：
  服务依赖经闭包绑定，thread_id/user_id 经 InjectedState 从图 state 注入，
  异常降级为占位文案「工具执行失败。」。
- MCP 工具（外部服务）已是 BaseTool，直接并入。

InjectedState 是 InjectedToolArg 子类：LangChain 的 tool_call_schema 自动
排除注入参数（LLM 看不到），ToolNode 执行时从 graph state 注入。
"""

import logging
from typing import Annotated

from langchain_core.tools import BaseTool, StructuredTool
from langgraph.prebuilt import InjectedState
from pydantic import Field

from bot.core.tools.search_chat_history import search_chat_history
from bot.core.tools.user_memory import recall_user_memory, remember_user_memory

logger = logging.getLogger(__name__)

SEARCH_TOOL_DESCRIPTION = (
    "检索群聊历史消息。双模式："
    "（1）语义检索——当用户询问之前讨论过的话题、事实、决定、约定时用 query 检索最相关内容；"
    "（2）按人/按内容/按时间属性检索——当用户问『某人说过什么』『谁说过xx』『bot 回复过谁』"
    "或『最近一段时间内』时，用 user_name / content_keyword / start_time / end_time / hours"
    "精确过滤（更快更准；不受当前群限制，跨全部群返回，来源群标注在结果里）。"
)

REMEMBER_TOOL_DESCRIPTION = (
    "保存当前用户的持久性个人信息（名字、偏好、习惯、背景等）。"
    "当用户提到新的持久事实时调用；更新已有记忆时直接以相同 key 覆盖。"
)

RECALL_TOOL_DESCRIPTION = (
    "检索当前用户的持久记忆（名字、偏好、习惯、背景等）。"
    "当需要用户的个人信息、或回想之前提到过的用户事实时使用。"
    "keyword 留空返回全部记忆，否则按 key/value 模糊匹配。"
)


def _make_search_tool(rag_service) -> BaseTool:
    async def _run(
        query: Annotated[str, Field(
            description="要检索的问题或关键词，用中文表述（语义检索模式；user_name/content_keyword 非空时忽略）",
        )],
        user_name: Annotated[str, Field(
            description="可选：指定涉及的用户昵称，模糊匹配 TA 的发言或 bot 给 TA 的回复",
        )] = "",
        hours: Annotated[int, Field(
            description="可选：只看最近 N 小时内的消息（相对窗口，与 start_time 二选一）",
        )] = 0,
        content_keyword: Annotated[str, Field(
            description="可选：按内容包含的关键词过滤，用于查『谁说过 xx』",
        )] = "",
        start_time: Annotated[str, Field(
            description="可选：时间窗口起始，ISO 格式 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS",
        )] = "",
        end_time: Annotated[str, Field(
            description="可选：时间窗口结束，ISO 格式 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS",
        )] = "",
        thread_id: Annotated[str, InjectedState("thread_id")] = "",
    ) -> str:
        try:
            return await search_chat_history(
                query, rag_service, thread_id, user_name, hours,
                content_keyword, start_time, end_time,
            )
        except Exception:
            logger.exception("search_chat_history failed")
            return "工具执行失败。"

    # 注意：async 函数必须走 coroutine= 参数（@tool 装饰器同款路由），
    # StructuredTool.from_function(func=<async>) 不会自动把异步函数挂到 coroutine，
    # 那样同步 _run() 会返回协程对象而非执行。
    return StructuredTool.from_function(
        coroutine=_run,
        name="search_chat_history",
        description=SEARCH_TOOL_DESCRIPTION,
    )


def _make_memory_tools(memory_store) -> list[BaseTool]:
    async def _remember(
        key: Annotated[str, Field(
            description='记忆的语义标签，如 "喜欢的食物"',
        )],
        value: Annotated[str, Field(
            description="记忆内容，中文表述",
        )],
        user_id: Annotated[str, InjectedState("user_id")] = "",
    ) -> str:
        try:
            return await remember_user_memory(key, value, memory_store, user_id)
        except Exception:
            logger.exception("remember_user_memory failed")
            return "工具执行失败。"

    async def _recall(
        keyword: Annotated[str, Field(
            description="检索关键词，按 key/value 模糊匹配；留空返回全部记忆",
        )] = "",
        user_id: Annotated[str, InjectedState("user_id")] = "",
    ) -> str:
        try:
            return await recall_user_memory(keyword, memory_store, user_id)
        except Exception:
            logger.exception("recall_user_memory failed")
            return "工具执行失败。"

    return [
        StructuredTool.from_function(
            coroutine=_remember, name="remember_user_memory", description=REMEMBER_TOOL_DESCRIPTION,
        ),
        StructuredTool.from_function(
            coroutine=_recall, name="recall_user_memory", description=RECALL_TOOL_DESCRIPTION,
        ),
    ]


def build_tools(rag_service=None, memory_store=None, mcp_tools=None) -> list[BaseTool]:
    """组装当前可用工具列表（BaseTool）。

    - rag_service 存在且启用 → search_chat_history
    - memory_store 存在 → remember/recall_user_memory
    - mcp_tools（BaseTool 列表）→ 直接并入
    """
    tools: list[BaseTool] = []
    if rag_service is not None and rag_service.enabled:
        tools.append(_make_search_tool(rag_service))
    if memory_store is not None:
        tools += _make_memory_tools(memory_store)
    tools += list(mcp_tools or [])
    return tools
