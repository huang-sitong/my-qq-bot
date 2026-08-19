"""MCP 加载端到端测试：本地 FastMCP stdio server（不打外网）。

验证 load_mcp_tools → ToolNode 执行 → ToolMessage 内容正确，
以及单个 server 加载失败降级跳过、不阻断。
"""

import asyncio
import logging
import sys
from typing import ClassVar

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime

from bot.package.mcp import load_mcp_tools
from bot.package.orchestration.graph import _tool_error_message

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

DIE_SERVER_CODE = '''
from mcp.server.fastmcp import FastMCP
import os

mcp = FastMCP("die")

@mcp.tool()
def die(x: int) -> str:
    """Kill the stdio subprocess mid-call to simulate a transport failure."""
    os._exit(1)

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


def test_mcp_load_failure_logs_class_name_only(monkeypatch, caplog):
    """加载失败日志绝不包含异常 repr——防 Tavily URL/密钥泄漏到日志。"""
    from bot.package.mcp import client as client_mod

    class FakeClient:
        connections: ClassVar[dict] = {"tavily": {"transport": "streamable_http"}}

        async def get_tools(self, server_name=None):
            raise RuntimeError("https://mcp.tavily.com/mcp/?tavilyApiKey=SECRETLEAK")

    monkeypatch.setattr(client_mod, "MultiServerMCPClient", lambda *a, **k: FakeClient())

    with caplog.at_level(logging.ERROR, logger="bot.package.mcp.client"):
        tools = asyncio.run(load_mcp_tools(
            {"tavily": {"transport": "streamable_http"}}))
    assert tools == []
    assert "SECRETLEAK" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_mcp_transport_failure_degrades(tmp_path):
    """MCP 工具调用中途子进程死亡（传输层失败）→ ToolNode 降级为「工具执行失败。」。

    验证 handle_tool_errors=_tool_error_message 兜住 MCP 传输层异常（此处为
    McpError），而不是让它中断整轮对话；异常只按类名记日志，不泄漏 URL。
    """
    server = tmp_path / "die_server.py"
    server.write_text(DIE_SERVER_CODE, encoding="utf-8")

    async def scenario():
        servers = {
            "die": {
                "transport": "stdio",
                "command": sys.executable,
                "args": [str(server)],
            },
        }
        tools = await asyncio.wait_for(load_mcp_tools(servers), timeout=30)
        assert {t.name for t in tools} == {"die"}

        node = ToolNode(tools, handle_tool_errors=_tool_error_message)
        call = AIMessage(content="", tool_calls=[
            {"name": "die", "args": {"x": 1}, "id": "call_die", "type": "tool_call"},
        ])
        result = await asyncio.wait_for(
            node.ainvoke({"messages": [call]}, runtime=Runtime()), timeout=30)
        msg = result["messages"][0]
        assert isinstance(msg, ToolMessage)
        assert msg.status == "error"
        assert msg.content == "工具执行失败。"

    asyncio.run(scenario())
