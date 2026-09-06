import asyncio, sys, os
sys.path.insert(0, "src")
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SRC = os.path.abspath("src")

async def main():
    params = StdioServerParameters(
        command=".venv/Scripts/python.exe", args=["-m", "stata_mcp.server"],
        cwd=os.getcwd(), env={**os.environ, "PYTHONPATH": SRC},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await session.call_tool("stata_run", {"code": "sysuse auto, clear"})
            r = await session.call_tool("stata_run", {"code": "regress mpg weight price"})
            sc = r.structured_content
            print("[structured_content 是否非空]:", sc is not None)
            if sc:
                print("  cmd:", sc.get("cmd"))
                print("  系数:", [c["var"] for c in sc.get("coefs", [])])
                print("  scalars keys:", sorted(sc.get("scalars", {}).keys())[:8])
            print("  text 首行:", r.content[0].text.strip().split(chr(10))[0][:50])

asyncio.run(main())
