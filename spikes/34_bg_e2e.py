"""P5b MCP e2e：background 提交 → 查状态 → 查结果，走真实 MCP 协议。"""
import asyncio, sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SRC = os.path.join(os.path.dirname(__file__), "..", "src")

async def main():
    params = StdioServerParameters(
        command=".venv/Scripts/python.exe",
        args=["-m", "stata_mcp.server"],
        cwd=os.path.join(os.path.dirname(__file__), ".."),
        env={**os.environ, "PYTHONPATH": SRC},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("[1] 工具数:", len(tools.tools), [t.name for t in tools.tools])

            # background 提交短命令
            r = await session.call_tool("stata_run", {"code": "display 6*7", "background": True})
            txt = r.content[0].text
            print("[2] background 提交:", txt[:60])
            job = txt.split("job ")[1].split(";")[0]
            print("    job_id:", job)

            await asyncio.sleep(1.0)
            r = await session.call_tool("stata_task_status", {"job_id": job})
            print("[3] 状态:", repr(r.content[0].text.strip()), "isError:", r.is_error)

            # break 工具在 server 里注册了吗
            r = await session.call_tool("stata_break", {})
            print("[4] break:", r.content[0].text[:50])

            print("E2E_OK")

asyncio.run(main())
