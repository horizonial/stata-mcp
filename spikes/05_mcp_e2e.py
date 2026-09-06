"""P2 端到端：走真实 MCP stdio 协议，连接 server 并调用 stata_run。

用 .venv 跑： .venv/Scripts/python.exe spikes/05_mcp_e2e.py
用 mcp 官方 client 会话，spawn server 子进程，完成 initialize -> tools/list -> tools/call。
"""
import asyncio
import sys, os

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, SRC)

async def main():
    params = StdioServerParameters(
        command=".venv/Scripts/python.exe",
        args=["-m", "stata_mcp.server"],
        cwd=os.path.join(os.path.dirname(__file__), ".."),
        env={**os.environ, "PYTHONPATH": SRC},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print("[1] initialize OK:", init.server_info.name, init.server_info.version)

            tools = await session.list_tools()
            print("[2] tools/list:", [t.name for t in tools.tools])

            # 正常命令
            r1 = await session.call_tool("stata_run", {"code": "display 2+2"})
            print("[3] stata_run(display 2+2):")
            print("    isError:", r1.is_error)
            print("    content:", r1.content[0].text.strip())

            # 出错命令
            r2 = await session.call_tool("stata_run", {"code": "regress no_such_var"})
            print("[4] stata_run(regress no_such_var):")
            print("    isError:", r2.is_error)
            print("    content:", r2.content[0].text.strip())

            # 中文 + 持久状态验证
            await session.call_tool("stata_run", {"code": "scalar _x = 40 + 2"})
            r3 = await session.call_tool("stata_run", {"code": "display _x"})
            print("[5] 持久状态 scalar _x:", r3.content[0].text.strip(), "(期望 42)")

            print("E2E_OK")

asyncio.run(main())
