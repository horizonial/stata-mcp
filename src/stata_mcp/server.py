"""MCP stdio 服务器组装与入口（D6：薄服务器 + 注册表）。

用官方 mcp SDK 的 low-level Server：``on_list_tools`` / ``on_call_tool``
两个钩子直接对齐本项目的 TOOLS 注册表（每把工具自带 JSON schema），
避免 FastMCP/MCPServer 那种"按 Python 函数签名反推 schema"的模型。

启动：python -m stata_mcp.server（或安装后运行命令 stata-mcp）。
引擎惰性：import / 列工具不占 license，首次调用 stata_run 才拉起 pystata。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import mcp.types as types
from mcp.server.lowlevel.server import Server
from mcp.server.stdio import stdio_server

from . import __version__
from .tools import TOOLS  # noqa: F401  # 导入即触发各工具模块注册进 TOOLS


def make_context() -> SimpleNamespace:
    """装配会话上下文：工具 handler 通过 ctx.backend 取执行会话（Session）。

    这是唯一的装配点（P6 定案）：换 backend / 换会话策略只改这一处。P10 起
    ctx.backend 是一个 Session（懒启动的 worker 子进程代理），默认会话 "default"。

    P0-3：从 config[security].max_sessions 应用会话上限（每个会话 = 一个 license
    席位），应用一次即可（manager 是进程内单例）。
    """
    from .config import get_security, load_config
    from .session import get_manager

    manager = get_manager()
    _apply_max_sessions(manager)
    return SimpleNamespace(backend=manager.get_or_create("default"))


_max_sessions_applied = False


def _apply_max_sessions(manager) -> None:
    global _max_sessions_applied
    if _max_sessions_applied:
        return
    from .config import get_security, load_config

    cfg = load_config()
    limit = get_security(cfg, "max_sessions", 0) or 0
    manager.set_max_sessions(int(limit))
    _max_sessions_applied = True


async def _list_tools(ctx, params) -> types.ListToolsResult:
    tools = [
        types.Tool(name=name, description=tool.description, input_schema=tool.input_schema)
        for name, tool in sorted(TOOLS.items())
    ]
    return types.ListToolsResult(tools=tools)


async def _call_tool(req_ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
    tool = TOOLS.get(params.name)
    if tool is None:
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=f"unknown tool: {params.name}")],
            is_error=True,
        )

    arguments = dict(params.arguments or {})
    try:
        # 会话上下文（带 backend）交给 handler；放到线程里执行，避免长 Stata
        # 命令卡住事件循环。
        envelope = await asyncio.to_thread(tool.handler, arguments, make_context())
    except Exception as exc:  # 基础设施异常（引擎起不来、pystata 报错等）
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=f"stata_run failed: {exc!r}")],
            is_error=True,
        )

    # rc != 0 视为该次调用出错（内容仍返回，便于 agent 看到 Stata 的报错文本）。
    # 结构化结果放进 structured_content（P9 修复：之前只返回 text，把 coefs/
    # scalars/error_class/graphs 全丢弃了，agent 通过 MCP 拿不到结构化结果）。
    # error_class 也拼进 meta 一并暴露，便于 agent 判断错误类型。
    meta = dict(envelope.meta or {})
    if envelope.error_class is not None:
        meta["error_class"] = envelope.error_class
    if envelope.graphs:
        meta["graphs"] = envelope.graphs
    if envelope.error is not None:
        meta["error"] = envelope.error

    # P0-2：图片（图导出）转 MCP ImageContent，agent 能直接看到；svg 文件较大
    # 但仍是文本可用的 ImageContent，png/pdf 走 data URI。
    content: list = [types.TextContent(type="text", text=envelope.text)]
    import base64

    for mime, data in envelope.images:
        content.append(
            types.ImageContent(
                type="image",
                data=base64.b64encode(data).decode("ascii"),
                mimeType=mime,
            )
        )

    # P1c：结构化审计（code 只存哈希不存原文）。
    try:
        from .audit import record

        record(
            tool=params.name,
            code=arguments.get("code") if isinstance(arguments.get("code"), str) else None,
            rc=envelope.rc,
            elapsed_ms=envelope.meta.get("elapsed_ms", 0.0),
            error_class=envelope.error_class,
            error_kind=(envelope.error or {}).get("kind"),
            truncated=bool(envelope.meta.get("truncated")),
            reset=bool(envelope.meta.get("session_reset")),
        )
    except Exception:
        pass

    return types.CallToolResult(
        content=content,
        structured_content=envelope.structured,
        is_error=envelope.rc != 0,
        _meta=meta,
    )


def build_server() -> Server:
    return Server(
        name="stata-mcp",
        version=__version__,
        instructions="Execute Stata code. All calls share one persistent in-process "
        "Stata session (data/scalars persist across calls).",
        on_list_tools=_list_tools,
        on_call_tool=_call_tool,
    )


async def _serve_stdio() -> None:
    server = build_server()
    init_options = server.create_initialization_options()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, init_options)


def _cleanup_on_exit() -> None:
    """正常退出时关闭所有会话（P14：graceful 清理，配合 worker 孤儿看门狗）。"""
    try:
        from .session import get_manager

        get_manager().close_all()
    except Exception:
        pass


def main() -> None:
    import atexit

    atexit.register(_cleanup_on_exit)
    asyncio.run(_serve_stdio())


if __name__ == "__main__":
    main()
