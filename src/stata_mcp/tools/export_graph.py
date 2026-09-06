"""stata_export_graph 工具：把内存中的图导出为图片文件（P5c）。

P5c 实测（spike35-37）关键结论：
- pystata 内嵌引擎在 Stata 18 上**能直接 graph export PNG/SVG**（无 mcp-stata
  调研里的 rc=5100 崩溃），不需要"graph save + 外部批处理"绕行；
- **路径必须用正斜杠**：Stata 里 ``\\`` 是转义符，绝对路径若含反斜杠会被
  误解析（``C:\\Users`` 的 ``\\U`` 等），导致 "file not found"。这里一律
  ``os.path`` 归一后 ``.replace("\\\\", "/")``。

导出到工作目录下的 ``_mcp_graphs/``（文件夹契约内，agent 可稳定访问），
返回绝对路径供 agent 用其它工具读取/展示。
"""
from __future__ import annotations

import os
import time

from ..envelope import Envelope
from ..guard.validate import is_valid_identifier
from ..output.smcl import strip_smcl
from . import register
from .run import _resolve_backend, _session_arg, invalid_session_result

_FORMATS = ("png", "svg", "pdf")

_STATA_EXPORT_GRAPH_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "session_id": {
            "type": "string",
            "description": "会话标识；省略用 'default'。",
        },
        "format": {
            "type": "string",
            "enum": list(_FORMATS),
            "description": "导出格式：png / svg / pdf。",
            "default": "png",
        },
        "name": {
            "type": "string",
            "description": "要导出的图名（graph dir 里列出的名字，如 Graph / g1）；"
            "省略 = 当前图。",
        },
        "filename": {
            "type": "string",
            "description": "输出文件名（不含目录，不含路径分隔符）；省略则自动生成。",
        },
    },
            "required": [],
}


def _graphs_dir() -> str:
    """图输出目录：工作目录下的 _mcp_graphs/。"""
    return os.path.join(os.getcwd(), "_mcp_graphs")


@register("stata_export_graph", _STATA_EXPORT_GRAPH_SCHEMA)
def stata_export_graph(arguments: dict, ctx=None) -> Envelope:
    """把内存中的图导出为图片文件，返回绝对路径（正斜杠）与大小。"""
    args = arguments if isinstance(arguments, dict) else {}
    fmt = args.get("format", "png")
    name = args.get("name", None)
    filename = args.get("filename", None)
    meta = {"tool": "stata_export_graph"}

    if fmt not in _FORMATS:
        return Envelope(
            text=f"error: format must be one of {', '.join(_FORMATS)}",
            structured=None, rc=1, error_class=None, graphs=[], meta=meta,
        )
    if name is not None:
        if not isinstance(name, str) or not is_valid_identifier(name):
            return Envelope(
                text="error: 'name' must be a valid graph name (letters/underscore, "
                "no quotes or spaces)",
                structured=None, rc=1, error_class=None, graphs=[], meta=meta,
            )
    if filename is not None:
        from ..guard.validate import is_safe_filename

        if not isinstance(filename, str) or not is_safe_filename(filename):
            return Envelope(
                text="error: 'filename' must be a safe single-segment filename",
                structured=None, rc=1, error_class=None, graphs=[], meta=meta,
            )

    # 输出路径：_mcp_graphs/ 目录 + 文件名
    out_dir = _graphs_dir()
    os.makedirs(out_dir, exist_ok=True)
    if filename:
        stem = filename
    else:
        stem = f"{name or 'graph'}_{int(time.time() * 1000)}"
    out_path = os.path.join(out_dir, f"{stem}.{fmt}")
    # Stata 路径用正斜杠（反斜杠会被当转义）
    stata_path = out_path.replace("\\", "/")

    # 语法：graph export "path"[, name(g1)] [replace]；name 与 replace 之间不加逗号。
    name_clause = f", name({name})" if name else ""
    command = f'graph export "{stata_path}"{name_clause} replace'

    sid, sid_err = _session_arg(args)

    if sid_err:

        return invalid_session_result("stata_export_graph", sid_err)

    backend = _resolve_backend(ctx, sid)
    result = backend.execute(command)
    text = strip_smcl(result.text)

    structured: dict | None = None
    images: list[tuple[str, bytes]] = []
    if result.rc == 0 and os.path.exists(out_path):
        size = os.path.getsize(out_path)
        structured = {
            "path": stata_path,
            "format": fmt,
            "size_bytes": size,
            "graph": name or "(current)",
        }
        # P0-2：读图片字节，交给 server 转 MCP ImageContent，多模态 agent 可直接看到。
        mime = {"png": "image/png", "svg": "image/svg+xml", "pdf": "application/pdf"}.get(fmt, "application/octet-stream")
        try:
            with open(out_path, "rb") as fh:
                images.append((mime, fh.read()))
        except OSError:
            pass

    return Envelope(
        text=text if text else f"graph exported to {stata_path}",
        structured=structured,
        rc=result.rc,
        error_class=None,
        graphs=[stata_path] if structured else [],
        meta=meta,
        images=images,
    )
