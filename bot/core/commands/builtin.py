"""V1 内置命令：help / ping / version / skills / skill / status。"""

import time
from functools import partial

from common.config import _parse_flag

from .model import Command, CommandContext, CommandResult, CommandServices
from .registry import CommandRegistry


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
    lines = [
        f"qq-bot {services.version}",
        f"运行时间：{_format_uptime(time.time() - services.started_at)}",
        f"LLM：{cfg.llm_model}",
        f"数据库目录：{cfg.db_dir}",
        f"RAG：{'开启' if services.rag_service is not None else '关闭'}",
        f"视觉：{'开启' if services.vision_service is not None else '关闭'}",
        f"MCP：{services.mcp_tool_count} 个工具",
        f"技能：{services.skill_registry.total if services.skill_registry else 0} 个",
        f"记忆：{'开启' if services.memory_store is not None else '关闭'}",
        f"自动回复：{'开启' if cfg.auto_reply else '关闭'}",
    ]
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
    return registry
