"""MCP server 配置文件加载（JSON + ${ENV_VAR} 插值）。

MCP 连接配置集中在一个可版本化、可评审的 JSON 文件（默认
``config/mcp_servers.json``），密钥一律用 ``${ENV_VAR}`` 占位从环境变量
插值，文件本身不含密钥。本模块只依赖 stdlib，``common`` 包保持轻量、
不触发 ``bot`` 的重型导入链（langchain / aiosqlite 等）。
"""

import json
import logging
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from bot.package.utils.paths import PROJECT_ROOT

logger = logging.getLogger(__name__)

_ENV_PLACEHOLDER = re.compile(r"\$\{(\w+)\}")


def _interpolate(value: Any, env: Mapping[str, str]) -> Any:
    """递归替换字符串值里的 ``${VAR}``；缺失环境变量 → 空串。"""
    if isinstance(value, str):
        return _ENV_PLACEHOLDER.sub(lambda m: env.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _interpolate(v, env) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate(v, env) for v in value]
    return value


def load_mcp_servers_from_file(path: str, *, env: Mapping[str, str]) -> dict[str, Any]:
    """读取 MCP 配置文件 → ``{server_name: Connection}``。

    文件支持 ``{"servers": {...}}`` 顶层键，或直接是 server 映射。
    相对路径按项目根解析；文件缺失/损坏/非 dict → 返回 {} 并告警（不崩）。
    ``env`` 提供 ``${VAR}`` 插值源（项目约定不读进程环境，由调用方
    传入 .env 内容——见 main.py 的 ``dotenv_values``）。
    """
    if not path:
        return {}
    p = Path(path)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    if not p.is_file():
        logger.warning("MCP 配置文件不存在，忽略：%s", p)
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning("MCP 配置文件损坏，忽略：%s（%s）", p, exc)
        return {}
    if isinstance(data, dict) and "servers" in data:
        data = data["servers"]
    if not isinstance(data, dict):
        logger.warning("MCP 配置文件格式非法（须为 object 或 {\"servers\": {...}}）：%s", p)
        return {}
    return _interpolate(data, env)
