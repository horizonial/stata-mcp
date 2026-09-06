"""stata_run 工具：把一段 Stata 代码交给会话执行，包装成 Envelope 返回。

P10 会话隔离：ctx.backend 现在是一个 Session（worker 子进程的代理）。执行与
结构化解析都在 worker 内完成（sfi 只能读 worker 进程内内存），主进程只拿回
SessionResult（text/rc/e_changed/structured）。
"""
from __future__ import annotations

import os
import re
import time

from ..envelope import Envelope
from ..output.smcl import strip_smcl
from ..output.truncate import truncate
from . import register

# session_id 白名单（P16 #2）：只允许简单标识符，防任意字符串创建会话/耗尽 license
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _as_bool(raw, default: bool = False) -> bool:
    """严格 bool 解析（P16b #4）：字符串 "false" 不得当 True。

    - None → default；
    - 真 bool → 原样；
    - 字符串 → 仅显式 'true'/'1'/'yes'/'on'（大小写不敏感）→ True，否则 False；
      这样 `background="false"`、`clear="false"` 都不会误伤。
    - 其它类型 → default（不信任）。
    """
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in ("true", "1", "yes", "on")
    return default

# MCP tool 入参 schema：本阶段只有一段 code 字符串
_STATA_RUN_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "code": {
            "type": "string",
            "description": "要交给 Stata 执行的命令或代码块（多行可包含循环/临时变量，"
            "共享同一持久会话的内存状态）。",
        },
        "background": {
            "type": "boolean",
            "description": "true 时命令在后台执行、立即返回 job_id，"
            "用 stata_task_status 轮询结果、stata_break 打断；false（默认）同步执行。"
            "长命令（bootstrap/混合模型等）建议用 true。",
            "default": False,
        },
        "session_id": {
            "type": "string",
            "description": "会话标识；省略用 'default'。不同 session 相互隔离（各自独立 Stata）。",
        },
        "restricted": {
            "type": "boolean",
            "description": "受限模式：拦截 shell 逃逸(shell/winexec/!)、文件删除(erase)、"
            "越权文件路径。省略用 config[security].restricted_mode（默认 false）。",
        },
    },
    "required": ["code"],
}


def _resolve_session(ctx, session_id: str | None = None):
    """取执行会话：按 session_id 从 ctx.manager 路由（P16 #2 真多会话）。

    ctx 已惰性化（P16c）：backend/session_id 可能为 None，只有显式 sid 或 default
    才真正 get_or_create。
    """
    manager = getattr(ctx, "manager", None)
    if manager is not None:
        sid = session_id or getattr(ctx, "session_id", None) or "default"
        return manager.get_or_create(sid)
    backend = getattr(ctx, "backend", None)
    if backend is not None:
        return backend
    from ..session import get_manager

    return get_manager().get_or_create("default")


def _resolve_backend(ctx, session_id: str | None = None):
    """工具通用会话解析：支持显式 session_id（P16b #1）。

    session_id 调用方须已校验（_normalize_session_id），非法在此不再静默回退。
    """
    return _resolve_session(ctx, session_id)


def _normalize_session_id(raw) -> str | None:
    """校验 session_id；返回 None 表示"未提供或非法"（调用方须区分）。"""
    if not isinstance(raw, str) or not raw.strip():
        return None
    return raw.strip() if _SESSION_ID_RE.match(raw.strip()) else None


def _session_arg(args):
    """从工具入参取 session_id → (sid, error)。error 非 None 表示"给了但非法"。

    用于显式报错而非静默回退（P16c：非法 id 如 `../ideaA` 不应悄悄当 default）。
    """
    if not isinstance(args, dict):
        return None, None
    raw = args.get("session_id")
    if raw is None:
        return None, None
    if not isinstance(raw, str) or not raw.strip():
        return None, "session_id must be a non-empty string"
    s = raw.strip()
    if not _SESSION_ID_RE.match(s):
        return None, f"invalid session_id {s!r} (allowed: [A-Za-z0-9_-], 1-64)"
    return s, None


def invalid_session_result(tool: str, err: str) -> Envelope:
    """非法 session_id 的统一拒绝 Envelope（P16d：全工具严格，不静默回退）。"""
    return Envelope(
        text=f"error: {err}",
        structured=None, rc=1, error_class=None, graphs=[], meta={"tool": tool},
    )


def enrich_structured(structured, code: str, result) -> dict:
    """给结构化结果附 provenance（P11/P0-4）：command_hash + 数据指纹 +
    exec_seq + 可复现 do-file。task_status（后台）与 stata_run（同步）共用。"""
    if structured is None or not (
        getattr(result, "command_hash", None)
        or getattr(result, "data_signature", None)
        or getattr(result, "data_load_cmd", None)
    ):
        return structured
    provenance = {}
    if result.command_hash:
        provenance["command_hash"] = result.command_hash
    if result.data_signature:
        provenance["data_signature"] = result.data_signature
    if result.exec_seq:
        provenance["exec_seq"] = result.exec_seq
    if getattr(result, "data_load_cmd", None) or code:
        preamble = result.data_load_cmd
        do_file = (preamble + "\n" if preamble else "") + code
        provenance["do_file"] = do_file
    return {**structured, "provenance": provenance}


def _check_restricted(code: str, arguments: dict) -> Envelope | None:
    """受限模式校验（P12）。通过返回 None；被拦截返回拒绝 Envelope。

    P16 修复：**在 background 分支之前执行**——否则后台任务可绕过 restricted
    提交 shell（审计发现 #1）。
    """
    from ..config import get_security, load_config

    cfg_enforced = bool(get_security(load_config(), "restricted_mode", False))
    # P16c：restricted 语义是"只增不减"——config 开了就不能被工具参数关掉。
    # 工具传 restricted=true 是显式加强；传 false/"garbage" 均不能削弱 config。
    restricted = cfg_enforced or _as_bool(arguments.get("restricted"), False)
    if not restricted:
        return None
    from ..guard.data_path import DataPathAuditor
    from ..guard.restrict import restrict

    cfg = load_config()
    auditor = DataPathAuditor(
        allowed_dirs=[os.getcwd()] + list(get_security(cfg, "allowed_data_dirs") or []),
        enable_url_guard=bool(get_security(cfg, "enable_url_guard", True)),
        allowed_hosts=list(get_security(cfg, "allowed_hosts") or []),
        enable_dns_resolve=bool(get_security(cfg, "enable_url_dns_resolve", True)),
    )
    allowed, reason = restrict(code, auditor)
    if not allowed:
        return Envelope(
            text=f"error: {reason}",
            structured=None,
            rc=1,
            error_class=None,
            graphs=[],
            meta={"tool": "stata_run", "restricted": True},
        )
    return None


@register("stata_run", _STATA_RUN_SCHEMA)
def stata_run(arguments: dict, ctx=None) -> Envelope:
    """执行一段 Stata 代码（保持会话状态），返回清洗后的输出文本与 rc。"""
    args = arguments if isinstance(arguments, dict) else {}
    code = args.get("code", "")
    background = _as_bool(args.get("background"), False)  # P16b #4：字符串 "false"→False

    if not isinstance(code, str) or not code.strip():
        return Envelope(
            text="error: 'code' must be a non-empty string",
            structured=None,
            rc=1,
            error_class=None,
            graphs=[],
            meta={"tool": "stata_run"},
        )

    # 受限模式（P12/P16）：**先于 background** 校验，后台任务同样拦截 shell 逃逸。
    blocked = _check_restricted(code, arguments if isinstance(arguments, dict) else {})
    if blocked is not None:
        return blocked

    sid, sid_err = _session_arg(args)  # 先校验 session_id（P16c）
    if sid_err:
        return Envelope(
            text=f"error: {sid_err}", structured=None, rc=1,
            error_class=None, graphs=[], meta={"tool": "stata_run"},
        )

    if background:
        # 后台执行：立即返回 job_id，命令在独立线程跑（仍受会话串行锁约束）。
        # P16b #1：后台任务可指定 session_id（不固定 default）。
        from ..tasks import get_runner

        job_id = get_runner().submit(code, sid)
        return Envelope(
            text=f"submitted background job {job_id}; poll with stata_task_status, "
            "interrupt with stata_break",
            structured=None,
            rc=0,
            error_class=None,
            graphs=[],
            meta={"tool": "stata_run", "job_id": job_id, "background": True},
        )

    try:
        session = _resolve_session(ctx, sid)  # sid 来自上面 _session_arg（已校验）
    except Exception as exc:  # SessionLimitExceeded 等：友好返回而非裸异常
        return Envelope(
            text=f"error: {exc}",
            structured=None,
            rc=1,
            error_class=None,
            graphs=[],
            meta={"tool": "stata_run"},
        )
    t0 = time.perf_counter()
    result = session.execute(code)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    text = strip_smcl(result.text)
    text, truncated = truncate(text)  # P0-1：防超大输出撑爆上下文

    # 结构化结果由 worker 内完成（result.structured），这里只组装。
    error_class: str | None = None
    if result.rc != 0:
        from ..output.errors import classify_error

        error_class = classify_error(result.rc, text)

    # 来源追溯（P11/P0-4）：provenance（含可复现 do-file）。agent 数值接地。
    structured = enrich_structured(result.structured, code, result)

    meta = {"tool": "stata_run", "elapsed_ms": round(elapsed_ms, 3)}
    if result.reset:
        meta["session_reset"] = True
    if truncated:
        meta["truncated"] = True
    # P15：崩溃重置时带出重放命令（最近若干条），agent 据此重建状态。
    if result.replay:
        cmds = [e.get("cmd") for e in result.replay if e.get("cmd")]
        meta["replay"] = cmds[-30:]  # 截断，避免 meta 过大
        meta["replay_full"] = len(cmds)

    # 报错托底（P13/P16）：统一结构化错误对象——命令报错/超时/引擎崩溃/启动失败。
    error: dict | None = None
    if result.error_kind == "timeout":
        error = {"kind": "timeout", "message": text or "command was interrupted after timeout"}
    elif result.error_kind == "crashed":
        error = {"kind": "crashed", "message": text or "Stata session crashed"}
    elif result.error_kind == "start_failed":
        error = {"kind": "start_failed", "message": text or "Stata engine failed to start"}
    elif result.rc != 0:
        error = {
            "kind": "command_failed",
            "rc": result.rc,
            "class": error_class,
            "message": text,
        }

    return Envelope(
        text=text,
        structured=structured,
        rc=result.rc,
        error_class=error_class,
        graphs=[],
        meta=meta,
        error=error,
    )
