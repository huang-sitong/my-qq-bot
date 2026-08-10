"""MCP server 配置解析（raw value → 连接配置）。

MCP JSON 解析独立于 ``BotConfig`` 存放，避免 MCP 域逻辑留在通用
``common/config.py``。本模块只依赖 stdlib，``common`` 包保持轻量、
不触发 ``bot`` 的重型导入链（langchain / aiosqlite 等）。
"""

import json
import logging

logger = logging.getLogger(__name__)


def parse_mcp_servers(raw: object) -> dict:
    """解析 BOT_MCP_SERVERS JSON；非法 JSON 或非 dict 降级为空。"""
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError):
        logger.warning("BOT_MCP_SERVERS 非法 JSON，忽略：%s", str(raw)[:200])
        return {}
    if not isinstance(data, dict):
        logger.warning("BOT_MCP_SERVERS 不是 JSON object，忽略")
        return {}
    return data
