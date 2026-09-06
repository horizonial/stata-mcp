"""stata_inspect_data 工具：查看当前数据（describe / summarize / codebook，P5a）。

安全（P4 L1）：``variables`` 里的每个名字都要过 ``validate_varname`` 白名单校验，
非法名字直接 rc=1 拒绝，绝不拼进命令（杜绝注入面）。

结构化（P3 风格，DESIGN §8 直读内存）：
- summarize：仅当恰好指定 1 个变量时，读该变量的 r(N)/r(mean)/r(sd)/r(min)/r(max)。
  多变量/全变量的逐变量统计本阶段不做——``summarize v1 v2`` 的 r() 只保留
  *最后一个*变量的统计，直接拿来当 v1 的会错；要逐变量得循环 execute，留待
  有明确需求再做（这里 structured 给 None）。
- describe：可选产出当前数据变量名列表 ``{"variables": [...]}``（经 sfi.Data
  直读内存；未指定 variables 时才是"当前全部变量"，指定时回显所请求的合法名字）。
- codebook：无稳定的 r()/e() 内存结果，structured 恒 None（文本已足够）。
"""
from __future__ import annotations

import time

from ..envelope import Envelope
from ..guard.validate import validate_varname
from ..output.smcl import strip_smcl
from . import register
from .run import _resolve_backend, _session_arg

_ACTIONS = ("describe", "summarize", "codebook")

_STATA_INSPECT_DATA_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": list(_ACTIONS),
            "description": "describe: 变量清单(短版)；summarize: 描述统计；"
            "codebook: 变量编码簿(compact)。",
            "default": "describe",
        },
        "variables": {
            "type": "array",
            "items": {"type": "string"},
            "description": "要查看的 Stata 变量名列表；省略/空列表 = 全部变量。"
            "名字须为合法 Stata 变量名（字母/下划线开头，长度 1–32）。",
        },
    },
    "required": [],
}


def _normalize_variables(variables) -> tuple[bool, str | None, list[str]]:
    """校验并规整 variables 入参 → (ok, error_text, varlist)。

    ok=False 时 error_text 给出可读拒绝原因（调用方直接 rc=1 返回）。
    """
    if variables is None:
        return True, None, []
    if isinstance(variables, str):
        # 宽容处理单个字符串（客户端偶尔把单元素当字符串传）
        variables = [variables]
    if not isinstance(variables, (list, tuple)):
        return False, "error: 'variables' must be a list of variable-name strings", []
    varlist: list[str] = []
    for v in variables:
        if not isinstance(v, str):
            return (
                False,
                f"error: variable name must be a string, got {type(v).__name__}",
                [],
            )
        try:
            validate_varname(v)  # L1 白名单：非法抛 ValueError
        except ValueError as exc:
            return False, f"error: refusing to pass invalid variable name into command: {exc}", []
        varlist.append(v)
    return True, None, varlist


def _build_command(action: str, varlist: list[str]) -> str:
    vl = " ".join(varlist) if varlist else ""
    if action == "describe":
        return f"describe {vl}, short" if varlist else "describe, short"
    if action == "summarize":
        return f"summarize {vl}" if varlist else "summarize"
    if action == "codebook":
        return f"codebook {vl}, compact" if varlist else "codebook, compact"
    raise ValueError(f"unhandled action: {action}")  # pragma: no cover（调用前已校验）


def _summarize_structured(snap: dict, varlist: list[str]) -> dict | None:
    """单变量 summarize 结构，从会话快照的 r_scalars 取（worker 内已读）。"""
    if len(varlist) != 1:
        return None
    r_scalars = snap.get("r_scalars") or {}
    keys = ("N", "mean", "sd", "min", "max")
    out = {k: r_scalars[k] for k in keys if k in r_scalars}
    if not out:
        return None
    return {"action": "summarize", "variable": varlist[0], **out}


def _describe_structured(snap: dict, varlist: list[str]) -> dict | None:
    """describe 结构 = 变量名清单，从会话快照的 variables 取（worker 内已读）。"""
    if varlist:
        return {"action": "describe", "variables": list(varlist)}
    variables = snap.get("variables")
    if not variables:
        return None
    return {"action": "describe", "variables": list(variables)}


@register("stata_inspect_data", _STATA_INSPECT_DATA_SCHEMA)
def stata_inspect_data(arguments: dict, ctx=None) -> Envelope:
    """查看当前数据：describe/summarize/codebook，可限定变量名列表。
    返回命令的清洗输出；summarize（单变量）或 describe 附结构化结果。"""
    args = arguments if isinstance(arguments, dict) else {}
    action = args.get("action", "describe")
    variables = args.get("variables", None)
    meta = {"tool": "stata_inspect_data"}

    if action not in _ACTIONS:
        return Envelope(
            text=(
                f"error: unknown action {action!r}; action must be one of "
                f"{', '.join(_ACTIONS)}"
            ),
            structured=None,
            rc=1,
            error_class=None,
            graphs=[],
            meta=meta,
        )

    ok, error_text, varlist = _normalize_variables(variables)
    if not ok:
        return Envelope(
            text=error_text or "error: invalid variables",
            structured=None,
            rc=1,
            error_class=None,
            graphs=[],
            meta=meta,
        )

    command = _build_command(action, varlist)
    session = _resolve_backend(ctx, _session_arg(args))
    t0 = time.perf_counter()
    result = session.execute(command)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    meta["elapsed_ms"] = round(elapsed_ms, 3)

    text = strip_smcl(result.text)

    structured: dict | None = None
    if result.rc == 0:
        try:
            snap = session.snapshot()  # worker 内读 r()/variables/shape
            if action == "summarize":
                structured = _summarize_structured(snap, varlist)
            elif action == "describe":
                structured = _describe_structured(snap, varlist)
            # codebook：无稳定结构化 → None
        except Exception:
            structured = None

    error_class: str | None = None
    if result.rc != 0:
        from ..output.errors import classify_error

        error_class = classify_error(result.rc, text)

    return Envelope(
        text=text,
        structured=structured,
        rc=result.rc,
        error_class=error_class,
        graphs=[],
        meta=meta,
    )
