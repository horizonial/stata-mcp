"""stata_get_results 工具：读取当前会话内存里的 e()/r() 结果（只读）。

P10 会话隔离：结构化读取全部由 worker 内完成（session.snapshot），主进程只拿
回已解析好的 dict。返回优先级：e() 估计结果（结构化回归表）> r() 标量/宏 >
数据集形状。
"""
from __future__ import annotations

from ..envelope import Envelope
from . import register
from .run import _resolve_backend

_STATA_GET_RESULTS_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "session_id": {
            "type": "string",
            "description": "会话标识；省略用 'default'。",
        },
    },
    "required": [],
}


@register("stata_get_results", _STATA_GET_RESULTS_SCHEMA)
def stata_get_results(arguments: dict, ctx=None) -> Envelope:
    """返回当前会话内存里的估计(e())/描述统计(r())/形状 结构化结果（只读）。"""
    meta = {"tool": "stata_get_results"}
    session = _resolve_backend(ctx)

    snap = session.snapshot()
    reset = snap.get("reset", False)

    structured = snap.get("structured")  # e() 估计（worker 内 try_parse）
    r_scalars = snap.get("r_scalars")
    r_macros = snap.get("r_macros")
    shape = snap.get("shape")

    # 组装：优先 e()，否则 r() 标量/宏
    if structured is not None:
        result = structured
    else:
        result = {}
        if r_scalars:
            result["r_scalars"] = r_scalars
        if r_macros:
            result["r_macros"] = r_macros
        if not result:
            result = None

    # 描述性 text（结构化才是重点）
    parts = []
    if structured is not None:
        parts.append(f"estimation results for {structured.get('cmd', '?')}")
    if r_scalars:
        parts.append(f"{len(r_scalars)} r() scalars")
    if shape:
        parts.append(f"dataset {shape.get('N')} obs x {shape.get('k')} vars")
    if reset:
        parts.append("session was reset (data lost)")
    text = "; ".join(parts) if parts else "(no results in current session)"

    if reset:
        meta["session_reset"] = True

    return Envelope(
        text=text,
        structured=result,
        rc=0,
        error_class=None,
        graphs=[],
        meta=meta,
    )
