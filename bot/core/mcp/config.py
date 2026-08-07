"""MCP server 连接配置构建（配置 → 连接字典）。

汇聚"要连哪些 server"的决策：用户配置的额外 server + 有 key 时自动注册的
Tavily 官方远程端点。纯函数、不依赖 ``BotConfig``，由 ``main.py`` 调用后
交给 ``load_mcp_tools``。Tavily 端点 URL 与 streamable_http transport 属于
MCP 域知识，故放在本模块而非通用 config。
"""


def build_mcp_connections(servers: dict, tavily_api_key: str) -> dict:
    """合并额外 MCP server 与 Tavily 远程端点。

    Args:
        servers: 用户配置的 ``{server_name: Connection}`` 字典。
        tavily_api_key: Tavily API key；非空白时自动注册官方
            streamable_http 端点，key 内嵌在 URL query 中。

    Returns:
        最终连接配置字典（不修改入参）。
    """
    servers = dict(servers)
    key = tavily_api_key.strip()
    if key:
        servers.setdefault("tavily", {
            "transport": "streamable_http",
            "url": f"https://mcp.tavily.com/mcp/?tavilyApiKey={key}",
        })
    return servers
