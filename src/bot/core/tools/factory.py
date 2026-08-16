"""工具统一层：把内部纯函数 + MCP 工具归一为 BaseTool 列表。

- 内部工具（RAG 检索、用户记忆）用 StructuredTool.from_function 包装：
  服务依赖经闭包绑定，thread_id/channel_id 经 InjectedState 从图 state 注入；
  user_id/user_name 由 LLM 按上下文显式传入，缺失时回退最近一条 HumanMessage
  元数据；异常降级为占位文案「工具执行失败。」。
- MCP 工具（外部服务）已是 BaseTool，直接并入。

InjectedState 是 InjectedToolArg 子类：LangChain 的 tool_call_schema 自动
排除注入参数（LLM 看不到），ToolNode 执行时从 graph state 注入。
"""

import logging
from pathlib import Path
from typing import Annotated

from langchain_core.messages import BaseMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.prebuilt import InjectedState
from pydantic import Field

from bot.core.tools.run_bash import run_bash
from bot.core.tools.search_chat_history import search_chat_history
from bot.core.tools.send_file import send_file
from bot.core.tools.user_memory import (
    recall_user_memory,
    remember_user_memory,
    resolve_memory_user_id,
)
from conversation.bash import BashConfig
from skill.tools import load_skill, unload_skill

logger = logging.getLogger(__name__)

SEARCH_TOOL_DESCRIPTION = (
    "检索群聊历史消息。双模式："
    "（1）语义检索——当用户询问之前讨论过的话题、事实、决定、约定时用 query 检索最相关内容；"
    "（2）按人/按内容/按时间属性检索——当用户问『某人说过什么』『谁说过xx』『bot 回复过谁』"
    "或『最近一段时间内』时，用 user_name / content_keyword / start_time / end_time / hours"
    "精确过滤（更快更准；不受当前群限制，跨全部群返回，来源群标注在结果里）。"
)

REMEMBER_TOOL_DESCRIPTION = (
    "保存用户的持久性个人信息（名字、偏好、习惯、背景等）。"
    "默认保存当前发言者；批内涉及其他发言者时用 user_name 指定。"
    "更新已有记忆时直接以相同 key 覆盖。"
)

RECALL_TOOL_DESCRIPTION = (
    "检索用户的持久记忆（名字、偏好、习惯、背景等）。"
    "默认检索当前发言者；批内涉及其他发言者时用 user_name 指定。"
    "keyword 留空返回全部记忆，否则按 key/value 模糊匹配。"
)

LOAD_SKILL_TOOL_DESCRIPTION = (
    "加载一个技能，返回其完整使用说明/规则正文。"
    "当用户需求匹配系统提示里某个技能描述（触发信号）时，先调用本工具取回正文再按其执行。"
    "技能加载后持续生效，直到调用 unload_skill 释放。"
)

UNLOAD_SKILL_TOOL_DESCRIPTION = (
    "释放一个已加载的技能，停止遵循其规则。技能不再需要（任务完成/话题偏离）时调用。幂等。"
)

BASH_TOOL_DESCRIPTION = (
    "在 bot 宿主上执行 bash 命令（Windows Git Bash / WSL/Linux bash）。"
    "主要用于运行技能（skill）中的脚本、配置技能所需环境"
    "（安装依赖/创建虚拟环境/设置环境变量等）。\n"
    "- command：要执行的 bash 命令字符串"
    "（每次调用独立新 shell，cd/export 不跨调用保持）\n"
    "- cwd：工作目录（绝对路径；留空为项目根目录）\n"
    "- timeout：可选，本次命令超时秒数（1..3600；默认 BOT_BASH_TIMEOUT）\n"
    "- 工作目录仅限白名单根目录内；危险命令会被拦截；输出截断；超时退出。\n"
    "- 返回「退出码 N」+ 输出；退出码非 0 表示失败，可调整命令重试。\n"
    "- 不确定当前环境或路径风格时，先执行 `pwd` 和 `ls` 确认当前目录与文件真实路径。"
)

SEND_FILE_TOOL_DESCRIPTION = (
    "把 bot 宿主上的单个本地文件发送到当前 QQ 会话。"
    "用于把 run_bash 下载/生成的结果（如 jmcomic 漫画、zip、pdf）交付给用户。"
    "- path：本地文件绝对路径，目录请先用 run_bash 打包成 zip/pdf 等单文件\n"
    "- name：可选，发送时显示的文件名；留空使用原文件名\n"
    "- 图片会作为图片消息发送，其他文件作为 QQ 群文件/私聊文件发送；"
    "文件路径必须位于允许的根目录内。\n"
    "- 如果不确定当前环境是 Windows 还是 Linux/WSL，先用 run_bash 执行 `pwd`：\n"
    "  * `pwd` 以 `/` 开头 = Linux/WSL 环境，path 必须传 `/home/...` 或 `/mnt/c/...`；\n"
    "  * `pwd` 输出盘符如 `C:\\` = Windows 环境，才可以使用 `C:\\...`。\n"
    "- 禁止凭空编造路径；发送前可用 `run_bash` 执行 `ls -l <路径>` 确认文件确实存在。"
)


def _make_bash_tool(cfg: BashConfig) -> BaseTool:
    async def _run(
        command: Annotated[str, Field(description="要执行的 bash 命令字符串")],
        cwd: Annotated[str, Field(description="工作目录（绝对路径；留空为项目根目录）")] = "",
        timeout: Annotated[int | None, Field(
            description="可选：本次命令超时秒数（1..3600；默认使用 BOT_BASH_TIMEOUT）",
            ge=1,
            le=3600,
        )] = None,
    ) -> str:
        try:
            return await run_bash(command, cwd, timeout, cfg=cfg)
        except Exception:
            logger.exception("run_bash failed")
            return "工具执行失败。"

    return StructuredTool.from_function(
        coroutine=_run, name="run_bash", description=BASH_TOOL_DESCRIPTION,
    )


def _make_send_file_tool(file_sender, roots: list[Path]) -> BaseTool:
    async def _run(
        path: Annotated[str, Field(
            description="要发送的本地文件绝对路径（必须是文件；目录请先打包）",
        )],
        name: Annotated[str, Field(
            description="可选：发送时显示的文件名；留空使用原文件名",
        )] = "",
        channel_id: Annotated[str, InjectedState("channel_id")] = "",
    ) -> str:
        try:
            return await send_file(
                path, name, channel_id, file_sender=file_sender, roots=roots,
            )
        except Exception:
            logger.exception("send_file failed")
            return "工具执行失败。"

    return StructuredTool.from_function(
        coroutine=_run, name="send_file", description=SEND_FILE_TOOL_DESCRIPTION,
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
        user_name: Annotated[str, Field(
            description="目标用户的显示昵称；留空表示当前发言者",
        )] = "",
        user_id: Annotated[str, Field(
            description="目标用户的平台 ID；仅在明确知道时填，通常留空由 user_name 解析",
        )] = "",
        messages: Annotated[list[BaseMessage] | None, InjectedState("messages")] = None,
    ) -> str:
        try:
            resolved_id, error = resolve_memory_user_id(
                user_id, user_name, messages
            )
            if error:
                return error
            return await remember_user_memory(
                key, value, memory_store, resolved_id
            )
        except Exception:
            logger.exception("remember_user_memory failed")
            return "工具执行失败。"

    async def _recall(
        keyword: Annotated[str, Field(
            description="检索关键词，按 key/value 模糊匹配；留空返回全部记忆",
        )] = "",
        user_name: Annotated[str, Field(
            description="目标用户的显示昵称；留空表示当前发言者",
        )] = "",
        user_id: Annotated[str, Field(
            description="目标用户的平台 ID；仅在明确知道时填，通常留空由 user_name 解析",
        )] = "",
        messages: Annotated[list[BaseMessage] | None, InjectedState("messages")] = None,
    ) -> str:
        try:
            resolved_id, error = resolve_memory_user_id(
                user_id, user_name, messages
            )
            if error:
                return error
            return await recall_user_memory(keyword, memory_store, resolved_id)
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


def _make_skill_tools(skill_registry) -> list[BaseTool]:
    async def _load(
        skill_name: Annotated[str, Field(description="技能名（见系统提示的技能索引）")],
    ) -> str:
        try:
            return await load_skill(skill_name, skill_registry)
        except Exception:
            logger.exception("load_skill failed")
            return "工具执行失败。"

    async def _unload(
        skill_name: Annotated[str, Field(description="技能名")],
    ) -> str:
        try:
            return await unload_skill(skill_name)
        except Exception:
            logger.exception("unload_skill failed")
            return "工具执行失败。"

    return [
        StructuredTool.from_function(
            coroutine=_load, name="load_skill", description=LOAD_SKILL_TOOL_DESCRIPTION,
        ),
        StructuredTool.from_function(
            coroutine=_unload, name="unload_skill", description=UNLOAD_SKILL_TOOL_DESCRIPTION,
        ),
    ]


def build_tools(rag_service=None, memory_store=None, mcp_tools=None,
                skill_registry=None, bash_config=None, file_sender=None,
                send_roots=None) -> list[BaseTool]:
    """组装当前可用工具列表（BaseTool）。

    - rag_service 存在且启用 → search_chat_history
    - memory_store 存在 → remember/recall_user_memory
    - skill_registry 非空 → load_skill/unload_skill
    - bash_config 存在且 enabled → run_bash
    - file_sender 存在且 send_roots 非空 → send_file
    - mcp_tools（BaseTool 列表）→ 直接并入
    """
    tools: list[BaseTool] = []
    if rag_service is not None and rag_service.enabled:
        tools.append(_make_search_tool(rag_service))
    if memory_store is not None:
        tools += _make_memory_tools(memory_store)
    if skill_registry is not None and skill_registry.names():
        tools += _make_skill_tools(skill_registry)
    if bash_config is not None and bash_config.enabled:
        tools.append(_make_bash_tool(bash_config))
    if file_sender is not None and send_roots:
        tools.append(_make_send_file_tool(file_sender, send_roots))
    tools += list(mcp_tools or [])
    return tools
