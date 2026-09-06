"""stata_load_data 工具：把数据文件载入 Stata 内存（P5a）。

安全（P4 L3）：先做集中路径审计，不通过直接拒绝、绝不构造命令。
- allowed_dirs 默认 = [os.getcwd()] + config[security].allowed_data_dirs
  （工作目录天然可读；cwd 之外需在配置里显式授权，否则 fail-closed 拒绝）。
- URL（含 ``://``）走 ``DataPathAuditor.check_url``，本地路径走 ``check_local_path``。

命令（按扩展名路由）：.dta → ``use``（``clear`` 入参决定加不加 `, clear`）；
.csv → ``import delimited ..., clear``；.xlsx/.xls → ``import excel ..., firstrow clear``；
其它 → 交给 Stata 用 ``use`` 自判。

结构化（P3 风格、DESIGN §8）：load 成功后把 ``_N`` / ``c(k)`` 拷成可被
``sfi.Scalar`` 读的真实 scalar 再取回，产出 ``{"source", N, k}``；读不到一律
静默降级为 None，绝不让解析失败导致工具调用失败。
"""
from __future__ import annotations

import os
import time

from ..config import get_security, load_config
from ..envelope import Envelope
from ..guard.data_path import DataPathAuditor
from ..output.smcl import strip_smcl
from . import register
from .run import _resolve_backend, _session_arg_loose

# 模块级配置缓存：分层配置是进程内静态的，读一次即可（load_config 每次读盘+合并，
# 多工具共享时不值得每个工具调用都重读）。需要时测试可把本变量置回 None 重新加载。
_cfg: dict | None = None

_STATA_LOAD_DATA_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "source": {
            "type": "string",
            "description": "要加载的数据文件路径或 https URL（.dta/.csv/.xlsx/.xls）。"
            "本地路径必须位于服务器工作目录或 [security].allowed_data_dirs 之内；"
            "路径不含双引号。",
        },
        "clear": {
            "type": "boolean",
            "description": "true 时用 `, clear` 无条件覆盖内存中已有数据；"
            "false（默认）时若当前数据未保存，Stata 可能拒绝覆盖。仅影响 .dta。",
            "default": False,
        },
    },
            "session_id": {
            "type": "string",
            "description": "会话标识；省略用 'default'。",
        },
"required": ["source"],
}


def _config() -> dict:
    global _cfg
    if _cfg is None:
        _cfg = load_config()
    return _cfg


def _auditor() -> DataPathAuditor:
    """按 cwd + 配置构造 L3 审计器（每次调用现算，cwd 以调用时为准）。"""
    cfg = _config()
    allowed = [os.getcwd()]
    allowed += [
        d
        for d in (get_security(cfg, "allowed_data_dirs") or [])
        if isinstance(d, str) and d.strip()
    ]
    return DataPathAuditor(
        allowed_dirs=allowed,
        enable_url_guard=bool(get_security(cfg, "enable_url_guard", True)),
        allowed_hosts=list(get_security(cfg, "allowed_hosts") or []),
    )


def _is_url(source: str) -> bool:
    """URL 判据：含 ``://``（与 guard/data_path.py 的 scheme 判据一致；Windows
    路径 C:\\ 与 UNC \\\\ 都不含 ://，不会被误判）。"""
    return "://" in source


def _extension(source: str) -> str:
    """取文件扩展名（小写，含点）。URL 只取 path 部分，query/fragment 不影响判型。"""
    if "://" in source:
        from urllib.parse import urlsplit

        path = urlsplit(source).path or ""
        return os.path.splitext(path)[1].lower()
    return os.path.splitext(source)[1].lower()


def _build_load_command(source: str, clear: bool) -> str:
    """按扩展名构造 Stata 载入命令。source 已通过审计，双引号已排除。"""
    quoted = f'"{source}"'
    ext = _extension(source)
    if ext == ".dta":
        suffix = ", clear" if clear else ""
        return f"use {quoted}{suffix}"
    if ext == ".csv":
        return f'import delimited "{source}", clear'
    if ext in (".xlsx", ".xls"):
        return f'import excel "{source}", firstrow clear'
    # 其它格式/URL：交给 Stata 的 use 自判（它能处理 http(s) 远程 .dta 等）
    return f"use {quoted}"


def _read_shape(source: str, snap: dict) -> dict | None:
    """从会话快照取数据集形状（N/k），拼 source。worker 内已读，主进程只组装。"""
    shape = snap.get("shape")
    if not shape:
        return None
    out: dict = {"source": source}
    if shape.get("N") is not None:
        out["N"] = shape["N"]
    if shape.get("k") is not None:
        out["k"] = shape["k"]
    return out


@register("stata_load_data", _STATA_LOAD_DATA_SCHEMA)
def stata_load_data(arguments: dict, ctx=None) -> Envelope:
    """载入数据集到 Stata 内存：本地 .dta/.csv/.xlsx（须在工作目录或授权目录内）
    或 https URL。返回载入命令的清洗输出与 {source, N, k} 结构化形状。"""
    args = arguments if isinstance(arguments, dict) else {}
    source = args.get("source", "")
    # P16b #4：clear 严格 bool（字符串 "false" 不得当 True——否则可能覆盖未保存数据）
    from .run import _as_bool

    clear = _as_bool(args.get("clear"), False)
    meta = {"tool": "stata_load_data"}

    if not isinstance(source, str) or not source.strip():
        return Envelope(
            text="error: 'source' must be a non-empty file path or URL",
            structured=None,
            rc=1,
            error_class=None,
            graphs=[],
            meta=meta,
        )
    if '"' in source:
        # 命令用双引号包裹路径，路径内含引号会造成命令注入，直接拒绝（fail-closed）
        return Envelope(
            text="error: source path must not contain double quotes",
            structured=None,
            rc=1,
            error_class=None,
            graphs=[],
            meta=meta,
        )

    # ---- L3 集中路径审计：不通过直接拒绝，不构造命令 --------------------
    auditor = _auditor()
    if _is_url(source):
        if not auditor.check_url(source):
            return Envelope(
                text=(
                    f"error: URL {source!r} failed the data path guard "
                    "(https only, no IP literal / userinfo, host whitelist if set); "
                    "refusing to load"
                ),
                structured=None,
                rc=1,
                error_class=None,
                graphs=[],
                meta=meta,
            )
        cmd_source = source
    else:
        if not auditor.check_local_path(source):
            return Envelope(
                text=(
                    f"error: path {source!r} is outside the allowed data directories "
                    "(server working directory + config[security].allowed_data_dirs); "
                    "refusing to load"
                ),
                structured=None,
                rc=1,
                error_class=None,
                graphs=[],
                meta=meta,
            )
        # 本地路径转绝对，消除对 Stata 当前目录的歧义（审计已按绝对路径判过包含）
        cmd_source = os.path.abspath(source)

    command = _build_load_command(cmd_source, clear)
    session = _resolve_backend(ctx, _session_arg_loose(args))
    t0 = time.perf_counter()
    result = session.execute(command)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    meta["elapsed_ms"] = round(elapsed_ms, 3)

    text = strip_smcl(result.text)

    structured: dict | None = None
    if result.rc == 0:
        # load 成功才读形状：失败时内存里还是旧数据，读出来是错的。
        try:
            structured = _read_shape(source, session.snapshot())
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
