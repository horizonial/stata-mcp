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
            await session.call_tool("stata_run", {"code": "twoway (scatter mpg weight), name(g1)"})
            r = await session.call_tool("stata_export_graph", {"format": "png", "name": "g1"})
            print("content types:", [c.type for c in r.content])
            for c in r.content:
                if c.type == "image":
                    print("image data len:", len(c.data), "mime:", c.mime_type)
                else:
                    print("text:", c.text[:60].replace(chr(10), ' '))

asyncio.run(main())
