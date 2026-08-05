"""MCP 加载端到端测试：本地 FastMCP stdio server（不打外网）。

验证 load_mcp_tools → ToolNode 执行 → ToolMessage 内容正确，
以及单个 server 加载失败降级跳过、不阻断。
"""

import asyncio
import sys

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime

from bot.core.mcp import load_mcp_tools

SERVER_CODE = '''
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("math")

@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two numbers"""
    return a + b

if __name__ == "__main__":
    mcp.run(transport="stdio")
'''


def _stdio_servers(server_file) -> dict:
    return {
        "math": {
            "transport": "stdio",
            "command": sys.executable,
            "args": [str(server_file)],
        },
    }


def test_mcp_tools_load_and_execute(tmp_path):
    server = tmp_path / "math_server.py"
    server.write_text(SERVER_CODE, encoding="utf-8")

    async def scenario():
        tools = await asyncio.wait_for(
            load_mcp_tools(_stdio_servers(server)), timeout=30)
        assert {t.name for t in tools} == {"add"}

        node = ToolNode(tools)
        call = AIMessage(content="", tool_calls=[
            {"name": "add", "args": {"a": 3, "b": 5}, "id": "call_add", "type": "tool_call"},
        ])
        # langgraph 1.2.2 起 ToolNode 直接调用需注入 Pregel Runtime（编译图内自动注入，
        # 直接调用缺省会抛 ValueError「Missing required config key 'N/A' for 'tools'」）。
        result = await asyncio.wait_for(
            node.ainvoke({"messages": [call]}, runtime=Runtime()), timeout=30)
        assert isinstance(result["messages"][0], ToolMessage)
        # MCP 工具结果经 langchain-mcp-adapters 转成 content-block 列表（非纯字符串），
        # 统一走 str() 做子串断言以兼容字符串 / 内容块两种形态。
        assert "8" in str(result["messages"][0].content)

    asyncio.run(scenario())


def test_mcp_server_load_failure_skips():
    servers = {
        "broken": {
            "transport": "stdio",
            "command": sys.executable,
            "args": ["-c", "import sys; sys.exit(1)"],
        },
    }

    async def scenario():
        tools = await asyncio.wait_for(load_mcp_tools(servers), timeout=30)
        assert tools == []

    asyncio.run(scenario())
