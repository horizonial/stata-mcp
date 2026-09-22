"""stata_run 工具：把一段 Stata 代码交给会话执行，包装成 Envelope 返回。

P10 会话隔离：ctx.backend 现在是一个 Session（worker 子进程的代理）。执行与
结构化解析都在 worker 内完成（sfi 只能读 worker 进程内内存），主进程只拿回
SessionResult（text/rc/e_changed/structured）。
"""
from __future__ import annotations

import math
import os
import re
import time
from pathlib import Path

from ..contract import EXECUTION_RECEIPT_SCHEMA_VERSION
from ..envelope import Envelope
from ..output.smcl import strip_smcl
from ..output.truncate import truncate
from . import register

# session_id 白名单（P16 #2）：只允许简单标识符，防任意字符串创建会话/耗尽 license
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_ARTIFACT_SETTLE_TIMEOUT_SECONDS = 1.0
_ARTIFACT_SETTLE_INTERVAL_SECONDS = 0.025


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
        "timeout_seconds": {
            "type": "number",
            "minimum": 0.1,
            "maximum": 3600,
            "description": "本次 Stata 执行的上限秒数（0.1–3600）。超时由 MCP 执行层"
            "形成 timed_out execution receipt；省略使用服务器默认值。",
        },
        "operation_attempt_id": {
            "type": "string",
            "description": "可选的上层执行 Attempt identity，只用于输出 provenance。",
        },
        "artifact_outputs": {
            "type": "array",
            "description": "本次命令声明的封闭文件输出；只能使用工作目录内安全相对路径。",
            "items": {
                "type": "object",
                "properties": {
                    "output_slot": {"type": "string"},
                    "relative_staging_path": {"type": "string"},
                    "artifact_kind": {"type": "string"},
                    "media_type": {"type": "string"},
                    "required": {"type": "boolean"},
                },
                "required": [
                    "output_slot",
                    "relative_staging_path",
                    "artifact_kind",
                    "media_type",
                ],
                "additionalProperties": False,
            },
            "default": [],
        },
    },
    "required": ["code"],
}


def _artifact_output_contracts(
    args: dict, *, root: Path | None = None
) -> tuple[list[dict], str | None]:
    raw = args.get("artifact_outputs", [])
    if not isinstance(raw, list):
        return [], "artifact_outputs must be an array"
    root = (root or Path.cwd()).resolve()
    contracts: list[dict] = []
    slots: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            return [], "each artifact output must be an object"
        values = {
            key: item.get(key)
            for key in (
                "output_slot",
                "relative_staging_path",
                "artifact_kind",
                "media_type",
            )
        }
        if any(not isinstance(value, str) or not value.strip() for value in values.values()):
            return [], "artifact output identity fields must be non-empty strings"
        slot = values["output_slot"].strip()
        relative = Path(values["relative_staging_path"])
        if slot in slots or relative.is_absolute() or ".." in relative.parts:
            return [], "artifact output slots must be unique safe relative paths"
        resolved = (root / relative).resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            return [], "artifact output path escapes the MCP working directory"
        slots.add(slot)
        contracts.append(
            {
                **values,
                "output_slot": slot,
                "relative_staging_path": relative.as_posix(),
                "source_path": str(resolved),
                "required": _as_bool(item.get("required"), True),
            }
        )
    return contracts, None


def _capture_artifact_outputs(
    contracts: list[dict], *, command_hash: str | None, operation_attempt_id: str | None
) -> tuple[list[dict], list[str]]:
    captured: list[dict] = []
    missing: list[str] = []
    settle_deadline = time.monotonic() + _ARTIFACT_SETTLE_TIMEOUT_SECONDS
    for contract in contracts:
        source = Path(contract["source_path"])
        settled_size = _settled_file_size(source, deadline=settle_deadline)
        if settled_size is None:
            if contract["required"]:
                missing.append(contract["output_slot"])
            continue
        captured.append(
            {
                **contract,
                "producer_locator": (
                    f"stata-command://{command_hash or 'unknown'}"
                    f"?attempt={operation_attempt_id or 'unbound'}"
                    f"#{contract['output_slot']}"
                ),
                "expected": True,
                "size_bytes": settled_size,
            }
        )
    return captured, missing


def _settled_file_size(source: Path, *, deadline: float) -> int | None:
    """Wait briefly for a completed Stata command's output to become stable.

    Windows file visibility and close propagation can lag the Stata completion
    signal by a few scheduler ticks. The bounded settle window never retries the
    Stata command; it only observes the declared staging path.
    """

    previous: tuple[int, int] | None = None
    while True:
        try:
            stat = source.stat()
            if source.is_file():
                current = (stat.st_size, stat.st_mtime_ns)
                if current == previous:
                    return stat.st_size
                previous = current
        except FileNotFoundError:
            previous = None
        if time.monotonic() >= deadline:
            return None
        time.sleep(_ARTIFACT_SETTLE_INTERVAL_SECONDS)


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


def _timeout_arg(args) -> tuple[float | None, str | None]:
    """解析单次执行超时；None 表示使用 Session 默认值。"""
    if not isinstance(args, dict) or "timeout_seconds" not in args:
        return None, None
    raw = args.get("timeout_seconds")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None, "timeout_seconds must be a number between 0.1 and 3600"
    value = float(raw)
    if not math.isfinite(value) or value < 0.1 or value > 3600:
        return None, "timeout_seconds must be a finite number between 0.1 and 3600"
    return value, None


def invalid_session_result(tool: str, err: str) -> Envelope:
    """非法 session_id 的统一拒绝 Envelope（P16d：全工具严格，不静默回退）。"""
    return Envelope(
        text=f"error: {err}",
        structured=None, rc=1, error_class=None, graphs=[], meta={"tool": tool},
    )


def enrich_structured(structured, code: str, result) -> dict:
    """给结构化结果附 provenance（P11/P0-4）：command_hash + 数据指纹 +
    exec_seq + 可复现 do-file。task_status（后台）与 stata_run（同步）共用。"""
    returned = dict(getattr(result, "return_state", None) or {})
    scalars = returned.get("r_scalars")
    if structured is None:
        if isinstance(scalars, dict) and scalars:
            from ..results.regression import GENERIC_RESULT_SOURCE_CAPABILITY

            catalog = []
            for name, value in sorted(scalars.items()):
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    continue
                if math.isfinite(number):
                    catalog.append(
                        {
                            # For a pure r-class command the returned scalars are the command's
                            # primary catalog, so preserve the established method-neutral key.
                            # The explicit return.* namespace is reserved for r() values merged
                            # beside a still-live e() estimation catalog below.
                            "source_key": f"scalar.{name}",
                            "value": number,
                            "statistic_kind": "stata_returned_scalar",
                            "locator": {
                                "locator_type": "R_SCALAR",
                                "name": f"r({name})",
                            },
                            "primitive_locators": [],
                        }
                    )
            if catalog:
                structured = {
                    "cmdline": code,
                    "result_source_capability": dict(
                        GENERIC_RESULT_SOURCE_CAPABILITY
                    ),
                    "result_catalog": {
                        "schema_version": "stata.result-catalog/v1",
                        "elements": catalog,
                    },
                    "return_state": returned,
                }
    elif isinstance(scalars, dict) and scalars:
        # An estimation command followed by a post-estimation command can leave both a valid
        # e() estimation state and a distinct r() return state.  The generic e-class parser
        # must not make the atomically captured r() values disappear.  Keep legacy e() source
        # keys unchanged and publish r() values under an explicit namespace so collisions such
        # as e(p) versus r(p) remain impossible.
        existing_catalog = structured.get("result_catalog")
        if isinstance(existing_catalog, dict):
            existing_elements = existing_catalog.get("elements")
            if isinstance(existing_elements, list):
                merged = list(existing_elements)
                existing_keys = {
                    item.get("source_key")
                    for item in merged
                    if isinstance(item, dict)
                }
                for name, value in sorted(scalars.items()):
                    try:
                        number = float(value)
                    except (TypeError, ValueError):
                        continue
                    source_key = f"return.scalar.{name}"
                    if math.isfinite(number) and source_key not in existing_keys:
                        merged.append(
                            {
                                "source_key": source_key,
                                "value": number,
                                "statistic_kind": "stata_returned_scalar",
                                "locator": {
                                    "locator_type": "R_SCALAR",
                                    "name": f"r({name})",
                                },
                                "primitive_locators": [],
                            }
                        )
                structured = {
                    **structured,
                    "result_catalog": {**existing_catalog, "elements": merged},
                    "return_state": returned,
                }
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


def build_execution_receipt(
    session,
    result,
    *,
    truncated: bool,
    structured_result_status: str | None = None,
) -> dict:
    """建立 MCP 原生 Stata 执行事实，不吸收上层 Operation/Finalization 职责。"""
    kind = getattr(result, "error_kind", None)
    if kind == "timeout":
        execution_status = "timed_out"
    elif kind == "crashed":
        execution_status = "crashed"
    elif kind == "start_failed":
        execution_status = "start_failed"
    elif result.rc == 0:
        execution_status = "succeeded"
    else:
        execution_status = "command_failed"

    structured_status = structured_result_status or getattr(
        result, "structured_result_status", None
    )
    if structured_status is None:
        structured_status = "complete" if result.structured is not None else "not_applicable"

    session_id = getattr(result, "session_id", None) or getattr(session, "id", None)
    session_generation = getattr(result, "session_generation", None)
    if session_generation is None:
        session_generation = getattr(session, "_generation", None)

    return {
        "schema_version": EXECUTION_RECEIPT_SCHEMA_VERSION,
        "executor_instance_id": getattr(result, "executor_instance_id", None),
        "session_id": session_id,
        "session_generation": session_generation,
        "exec_seq": getattr(result, "exec_seq", None),
        "execution_status": execution_status,
        "rc": result.rc,
        "raw_output_status": "truncated" if truncated else "complete",
        "structured_result_status": structured_status,
        "command_hash": getattr(result, "command_hash", None),
        "data_signature": getattr(result, "data_signature", None),
        "session_reset": bool(getattr(result, "reset", False)),
        "runtime_environment": dict(getattr(result, "runtime_environment", None) or {}),
        "supervision_proof": dict(getattr(result, "supervision_proof", None) or {}),
    }


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
    timeout_seconds, timeout_err = _timeout_arg(args)
    if timeout_err:
        return Envelope(
            text=f"error: {timeout_err}", structured=None, rc=1,
            error_class=None, graphs=[], meta={"tool": "stata_run"},
        )
    from ..session import SessionLimitExceeded

    try:
        session = _resolve_session(ctx, sid)  # sid 来自上面 _session_arg（已校验）
    except SessionLimitExceeded as exc:
        return Envelope(
            text=f"error: {exc}",
            structured=None,
            rc=1,
            error_class=None,
            graphs=[],
            meta={"tool": "stata_run"},
            error={"kind": "capacity_exceeded", "message": str(exc)},
        )
    except Exception as exc:  # 其它：友好返回而非裸异常
        return Envelope(
            text=f"error: {exc}",
            structured=None,
            rc=1,
            error_class=None,
            graphs=[],
            meta={"tool": "stata_run"},
        )
    bound_directory = getattr(session, "canonical_working_directory", None)
    artifact_root = Path(bound_directory) if bound_directory else Path.cwd()
    artifact_contracts, artifact_error = _artifact_output_contracts(
        args, root=artifact_root
    )
    if artifact_error:
        return Envelope(
            text=f"error: {artifact_error}",
            structured=None,
            rc=1,
            error_class=None,
            graphs=[],
            meta={"tool": "stata_run"},
            error={"kind": "invalid_artifact_output_contract", "message": artifact_error},
        )
    preexisting = [
        contract["output_slot"]
        for contract in artifact_contracts
        if Path(contract["source_path"]).exists()
    ]
    if preexisting:
        message = f"artifact staging outputs already exist: {sorted(preexisting)}"
        return Envelope(
            text=f"error: {message}",
            structured=None,
            rc=1,
            error_class=None,
            graphs=[],
            meta={"tool": "stata_run"},
            error={"kind": "artifact_staging_collision", "message": message},
        )

    if background:
        # 后台执行：立即返回 job_id，命令在独立线程跑（仍受会话串行锁约束）。
        # P16b #1：后台任务可指定 session_id（不固定 default）。
        from ..session import SessionLimitExceeded
        from ..tasks import TaskCapacityExceeded, get_runner

        try:
            job_id = get_runner().submit(
                code,
                sid,
                timeout=timeout_seconds,
                session=session,
                metadata={
                    "artifact_contracts": artifact_contracts,
                    "operation_attempt_id": (
                        str(args["operation_attempt_id"])
                        if isinstance(args.get("operation_attempt_id"), str)
                        else None
                    ),
                },
            )
        except (TaskCapacityExceeded, SessionLimitExceeded) as exc:
            return Envelope(
                text=f"error: {exc}",
                structured=None,
                rc=1,
                error_class=None,
                graphs=[],
                meta={"tool": "stata_run", "background": True},
                error={"kind": "capacity_exceeded", "message": str(exc)},
            )
        return Envelope(
            text=f"submitted background job {job_id}; poll with stata_task_status, "
            "interrupt with stata_break",
            structured=None,
            rc=0,
            error_class=None,
            graphs=[],
            meta={"tool": "stata_run", "job_id": job_id, "background": True},
        )

    t0 = time.perf_counter()
    result = (
        session.execute(code)
        if timeout_seconds is None
        else session.execute(code, timeout=timeout_seconds)
    )
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

    execution_receipt = build_execution_receipt(
        session,
        result,
        truncated=truncated,
        structured_result_status="complete" if structured is not None else None,
    )
    captured_outputs, missing_outputs = _capture_artifact_outputs(
        artifact_contracts,
        command_hash=result.command_hash,
        operation_attempt_id=(
            str(args["operation_attempt_id"])
            if isinstance(args.get("operation_attempt_id"), str)
            else None
        ),
    )
    if captured_outputs:
        meta["artifact_outputs"] = captured_outputs
    if missing_outputs:
        meta["artifact_output_contract_status"] = "missing_required"
        meta["missing_required_artifact_outputs"] = missing_outputs
    elif artifact_contracts:
        meta["artifact_output_contract_status"] = "complete"

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
        execution_receipt=execution_receipt,
    )
