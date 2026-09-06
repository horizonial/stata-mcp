"""stata_break 工具：打断当前正在执行的 Stata 命令（P5b）。

打断的是 backend 当前在跑的（可能是后台任务线程里的）命令，用
StataSO_SetBreak；不持 backend 锁，见 pystata_backend.interrupt 的说明。
"""
from __future__ import annotations

from ..envelope import Envelope
from . import register
from .run import _resolve_backend, _session_arg

_STATA_BREAK_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "session_id": {
            "type": "string",
            "description": "要打断的会话标识；省略用 'default'。",
        }
    },
    "required": [],
}


@register("stata_break", _STATA_BREAK_SCHEMA)
def stata_break(arguments: dict, ctx=None) -> Envelope:
    """打断指定会话正在运行的 Stata 命令（若有）；打断后命令 rc=1，引擎存活可继续。"""
    args = arguments if isinstance(arguments, dict) else {}
    backend = _resolve_backend(ctx, _session_arg(args))
    backend.interrupt()
    return Envelope(
        text="break signal sent; any running command will stop with rc=1",
        structured=None,
        rc=0,
        error_class=None,
        graphs=[],
        meta={"tool": "stata_break"},
    )
