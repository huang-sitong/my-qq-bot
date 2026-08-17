"""V1 内置命令：help / ping / version / skills / skill / status / auto_reply / clear / compact / mcp / context。"""

import time
from functools import partial

from langchain_core.messages import RemoveMessage

from common.config import _parse_flag
from common.constants import EXTERNAL_UPDATE_NODE
from context.utils import content_to_text, estimate_context_tokens

from .domain import Command, CommandContext, CommandResult
from .registry import CommandRegistry
from .services import CommandServices


async def _help(ctx: CommandContext, registry: CommandRegistry) -> CommandResult:
    if not ctx.args:
        lines = ["可用指令："]
        lines.extend(f"{cmd.usage} - {cmd.description}" for cmd in registry.commands())
        return CommandResult(text="\n".join(lines))
    command = registry.resolve(ctx.args[0])
    if command is None:
        return CommandResult(text=f"指令不存在：{ctx.args[0]}")
    permission = "管理员" if command.permission == "admin" else command.permission
    return CommandResult(
        text=f"{command.usage}\n{command.description}\n权限：{permission}"
    )


async def _ping(ctx: CommandContext) -> CommandResult:
    return CommandResult(text="Pong.")


async def _version(ctx: CommandContext) -> CommandResult:
    return CommandResult(text=f"qq-bot {ctx.services.version}")


async def _skills(ctx: CommandContext) -> CommandResult:
    registry = ctx.services.skill_registry
    if registry is None or registry.total == 0:
        return CommandResult(text="当前没有可用技能。")
    # 复用 LLM 索引的截断口径（skills_index_max），技能多时回复不超长
    return CommandResult(text=registry.index_text())


async def _skill(ctx: CommandContext) -> CommandResult:
    if len(ctx.args) != 1:
        return CommandResult(text="用法：/skill <name>")
    registry = ctx.services.skill_registry
    if registry is None:
        return CommandResult(text="技能功能未启用。")
    skill = registry.get_skill(ctx.args[0].lower())
    if skill is None:
        return CommandResult(text="技能不存在。")
    return CommandResult(text=f"{skill.description}\n\n{skill.body[:2000]}")


def _format_uptime(seconds: float) -> str:
    secs = max(0, int(seconds))
    hours, rem = divmod(secs, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours}小时{minutes}分{secs}秒"


async def _status(ctx: CommandContext) -> CommandResult:
    cfg = ctx.config
    services = ctx.services
    mcp_count = len(services.mcp_tool_names) or services.mcp_tool_count
    lines = [
        f"qq-bot {services.version}",
        f"运行时间：{_format_uptime(time.time() - services.started_at)}",
        f"LLM：{cfg.llm_model}",
        f"数据库目录：{cfg.db_dir}",
        f"RAG：{'开启' if services.rag_service is not None else '关闭'}",
        f"视觉：{'开启' if services.vision_service is not None else '关闭'}",
        f"MCP：{mcp_count} 个工具",
        f"技能：{services.skill_registry.total if services.skill_registry else 0} 个",
        f"记忆：{'开启' if services.memory_store is not None else '关闭'}",
        f"自动回复：{'开启' if cfg.auto_reply else '关闭'}",
    ]
    if services.metrics_provider is not None:
        metrics = services.metrics_provider()
        lines.append(f"队列深度：{metrics.get('queue_size', 0)}")
        lines.append(f"已处理消息：{metrics.get('processed', 0)}")
        lines.append(f"丢弃重复：{metrics.get('dropped_duplicates', 0)}")
    return CommandResult(text="\n".join(lines))


async def _auto_reply(ctx: CommandContext) -> CommandResult:
    cfg = ctx.config
    if not ctx.args:
        return CommandResult(text=f"auto_reply 当前状态：{'开启' if cfg.auto_reply else '关闭'}")
    if len(ctx.args) != 1:
        return CommandResult(text="用法：/auto_reply [on|off]")
    try:
        value = _parse_flag(ctx.args[0])
    except ValueError:
        return CommandResult(text="参数无效，用法：/auto_reply [on|off]")
    cfg.auto_reply = value
    return CommandResult(text=f"auto_reply 已{'开启' if value else '关闭'}。")


async def _clear(ctx: CommandContext) -> CommandResult:
    graph = ctx.services.graph
    if graph is None:
        return CommandResult(text="当前未启用对话图，无法清空。")
    config = {"configurable": {"thread_id": ctx.thread_id}}
    snapshot = await graph.aget_state(config)
    state = snapshot.values if snapshot is not None else {}
    messages = state.get("messages", [])
    updates = {
        "messages": [
            RemoveMessage(id=m.id) for m in messages if getattr(m, "id", None)
        ],
        "conversation_summary": "",
        "active_skills": [],
        "tool_rounds": 0,
    }
    await graph.aupdate_state(
        config, updates, as_node=EXTERNAL_UPDATE_NODE,
    )
    return CommandResult(text="已清空当前会话上下文。")


async def _compact(ctx: CommandContext) -> CommandResult:
    compactor = ctx.services.compactor
    if compactor is None:
        return CommandResult(text="当前未启用上下文压缩。")
    removed = await compactor.force_compact(ctx.thread_id)
    if removed == 0:
        return CommandResult(text="当前上下文较少，无需压缩。")
    return CommandResult(text=f"已提前压缩上下文，移除 {removed} 条历史消息。")


async def _mcp(ctx: CommandContext) -> CommandResult:
    names = ctx.services.mcp_tool_names
    count = ctx.services.mcp_tool_count
    if not names and count <= 0:
        return CommandResult(text="当前未加载 MCP 工具。")
    if not names:
        return CommandResult(text=f"已加载 {count} 个 MCP 工具。")
    lines = [f"已加载 {len(names)} 个 MCP 工具："]
    lines.extend(f"- {name}" for name in names)
    return CommandResult(text="\n".join(lines))


async def _context(ctx: CommandContext) -> CommandResult:
    graph = ctx.services.graph
    if graph is None:
        return CommandResult(text="当前未启用对话图，无法查看上下文占用。")
    config = {"configurable": {"thread_id": ctx.thread_id}}
    snapshot = await graph.aget_state(config)
    state = snapshot.values if snapshot is not None else {}
    messages = state.get("messages", [])
    summary = content_to_text(state.get("conversation_summary", "")).strip()
    active_skills = state.get("active_skills", [])
    persona = state.get("persona", "")
    total = estimate_context_tokens(
        messages,
        persona,
        summary,
        skill_registry=ctx.services.skill_registry,
        active_skills=active_skills,
    )
    window = ctx.config.llm_context_window
    trigger = int(window * ctx.config.summary_trigger_ratio)
    lines = [
        f"当前上下文占用：{total} / {window} tokens（{total / window:.1%}）",
        f"剩余空间：{max(0, window - total)} tokens",
        f"对话消息：{len(messages)} 条",
        f"当前摘要：{len(summary)} 字符",
        f"已加载技能：{len(active_skills)} 个",
    ]
    if total >= trigger:
        lines.append("已达自动压缩阈值，下一轮会自动触发摘要。")
    else:
        lines.append(f"距自动压缩阈值：{trigger - total} tokens。")
    return CommandResult(text="\n".join(lines))


def build_command_registry(services: CommandServices, prefix: str = "/") -> CommandRegistry:
    """按固定顺序注册 V1 内置命令。"""
    registry = CommandRegistry()
    registry.register(Command(
        name="help",
        description="查看指令帮助",
        usage=f"{prefix}help [command]",
        permission="everyone",
        handler=partial(_help, registry=registry),
    ))
    registry.register(Command(
        name="ping",
        description="检查 bot 是否在线",
        usage=f"{prefix}ping",
        permission="everyone",
        handler=_ping,
    ))
    registry.register(Command(
        name="version",
        description="显示项目版本",
        usage=f"{prefix}version",
        permission="everyone",
        handler=_version,
    ))
    registry.register(Command(
        name="skills",
        description="列出已加载技能",
        usage=f"{prefix}skills",
        permission="everyone",
        handler=_skills,
    ))
    registry.register(Command(
        name="skill",
        description="查看指定技能",
        usage=f"{prefix}skill <name>",
        permission="everyone",
        handler=_skill,
    ))
    registry.register(Command(
        name="status",
        description="显示安全运行状态",
        usage=f"{prefix}status",
        permission="admin",
        handler=_status,
    ))
    registry.register(Command(
        name="auto_reply",
        description="查看/设置全局自动回复开关",
        usage=f"{prefix}auto_reply [on|off]",
        permission="admin",
        handler=_auto_reply,
    ))
    registry.register(Command(
        name="clear",
        description="清空当前会话上下文（含已加载技能）",
        usage=f"{prefix}clear",
        permission="admin",
        handler=_clear,
    ))
    registry.register(Command(
        name="compact",
        description="提前总结并压缩当前会话上下文",
        usage=f"{prefix}compact",
        permission="admin",
        handler=_compact,
    ))
    registry.register(Command(
        name="mcp",
        description="查看已加载的 MCP 工具",
        usage=f"{prefix}mcp",
        permission="admin",
        handler=_mcp,
    ))
    registry.register(Command(
        name="context",
        description="查看当前上下文占用情况",
        usage=f"{prefix}context",
        permission="admin",
        handler=_context,
    ))
    return registry
