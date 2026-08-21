"""MCP 上下文工厂。"""

from __future__ import annotations

import logging

from bot.package.config import BotConfig

from .client import load_mcp_tools
from .config import load_mcp_servers_from_file

logger = logging.getLogger(__name__)


async def create_mcp_tools(config: BotConfig, env_vars: dict[str, str]) -> list:
    """按配置加载 MCP 工具；禁用或空配置返回 []。"""
    if not config.mcp_enabled:
        return []
    tools = await load_mcp_tools(
        load_mcp_servers_from_file(config.mcp_servers_file, env=env_vars),
        tool_name_prefix=config.mcp_tool_name_prefix,
    )
    logger.info("Loaded %d MCP tools", len(tools))
    return tools
