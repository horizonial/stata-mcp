"""stata_data_rows 工具：读当前数据集前 N 行，agent 直接"看"数据（P1b）。

为什么需要它：describe/summarize 给统计概览，但 agent 要基于实际取值做判断
（看变量长什么样、检查异常值）需要看原始行。worker 内 sfi.Data 直读，返回
结构化二维数组，内存受限（默认最多 20 行，字符串截断）。
"""
from __future__ import annotations

from ..envelope import Envelope
from . import register
from .run import _resolve_backend, _session_arg

_STATA_DATA_ROWS_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "rows": {
            "type": "integer",
            "description": "要读的行数（1-50，默认 10）。",
            "minimum": 1,
            "maximum": 50,
            "default": 10,
        }
    },
    "required": [],
}


@register("stata_data_rows", _STATA_DATA_ROWS_SCHEMA)
def stata_data_rows(arguments: dict, ctx=None) -> Envelope:
    """返回当前数据集前 N 行（结构化二维数组），供 agent 直接查看数据。"""
    args = arguments if isinstance(arguments, dict) else {}
    n = args.get("rows", 10)
    meta = {"tool": "stata_data_rows"}

    if not isinstance(n, int) or isinstance(n, bool):
        return Envelope(
            text="error: 'rows' must be an integer",
            structured=None, rc=1, error_class=None, graphs=[], meta=meta,
        )
    n = max(1, min(int(n), 50))

    session = _resolve_backend(ctx, _session_arg(args))
    preview = session.preview(n)
    if not preview or "variables" not in preview:
        return Envelope(
            text="no data loaded in the current session (load data first with stata_load_data)",
            structured=None, rc=1, error_class=None, graphs=[], meta=meta,
        )

    return Envelope(
        text=f"dataset: {preview.get('N')} obs x {len(preview.get('variables', []))} vars; "
        f"showing {preview.get('n_rows', 0)} rows",
        structured=preview,
        rc=0,
        error_class=None,
        graphs=[],
        meta=meta,
    )
