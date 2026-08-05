"""MCP 外部工具加载：连接多 MCP server，归一为 BaseTool 列表。

使用 langchain-mcp-adapters 的 MultiServerMCPClient。每个 MCP 工具调用时
自建 session（streamable_http=新 HTTP 会话），无需长期持有连接。
单个 server 加载失败降级跳过，不阻断 bot 启动。
"""

import logging

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

logger = logging.getLogger(__name__)


async def load_mcp_tools(servers: dict, *, tool_name_prefix: bool = False) -> list[BaseTool]:
    """从配置的 MCP servers 加载全部工具；单 server 失败跳过。

    Args:
        servers: ``{server_name: Connection}`` 连接配置字典（transport 可为
            streamable_http / stdio / sse / websocket）。
        tool_name_prefix: 为 True 时工具名加 ``<server>_`` 前缀，防多 server
            工具名冲突。

    Returns:
        LangChain BaseTool 列表；任一 server 加载失败被跳过（记日志）。
    """
    if not servers:
        return []
    client = MultiServerMCPClient(servers, tool_name_prefix=tool_name_prefix)
    tools: list[BaseTool] = []
    for name in client.connections:
        try:
            tools += await client.get_tools(server_name=name)
        except Exception:
            logger.exception("MCP server %s 加载失败，跳过", name)
    return tools
