"""MCP server 配置解析（env → 连接配置）。

env JSON 解析独立于 ``BotConfig`` 存放：dataclass 的 ``default_factory``
需要零参 callable，但 MCP 域逻辑不该留在通用 ``common/config.py``。本模块
只依赖 stdlib，``common`` 包保持轻量、不触发 ``bot`` 的重型导入链
（langchain / aiosqlite 等）。
"""

import json
import logging
import os

logger = logging.getLogger(__name__)


def load_mcp_servers_from_env() -> dict:
    """解析 BOT_MCP_SERVERS JSON；非法 JSON 或非 dict 降级为空。"""
    raw = os.getenv("BOT_MCP_SERVERS", "{}")
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        logger.warning("BOT_MCP_SERVERS 非法 JSON，忽略：%s", raw[:200])
        return {}
