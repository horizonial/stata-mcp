"""Stata session 生命周期的只读观察与显式关闭工具。

这些工具只控制 MCP 自己拥有的 Stata worker，不提交上层 Agent 的 Operation、
Run、Result、Evidence 或 current pointer。状态查询不创建 session；关闭按
session_id 幂等执行，并且不影响其他 session。
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from ..contract import (
    EXECUTOR_CAPABILITIES_SCHEMA_VERSION,
    SESSION_CONTROL_SCHEMA_VERSION,
    SESSION_OPEN_SCHEMA_VERSION,
)
from ..envelope import Envelope
from ..session import executor_instance_id, get_manager
from . import register
from .run import _session_arg, invalid_session_result


_SESSION_ID_PROPERTY = {
    "type": "string",
    "description": "要查询或关闭的明确 session 标识；不会隐式回退到 default。",
}

_STATUS_SCHEMA = {
    "type": "object",
    "properties": {"session_id": _SESSION_ID_PROPERTY},
    "required": ["session_id"],
}

_OPEN_SCHEMA = {
    "type": "object",
    "properties": {
        "session_id": _SESSION_ID_PROPERTY,
        "working_directory": {
            "type": "string",
            "description": "MCP host 工作目录下的相对目录；'.' 表示 host root。",
            "default": ".",
        },
    },
    "required": ["session_id"],
    "additionalProperties": False,
}

_CAPABILITIES_SCHEMA = {"type": "object", "additionalProperties": False}

_CLOSE_SCHEMA = {
    "type": "object",
    "properties": {
        "session_id": _SESSION_ID_PROPERTY,
        "reason": {
            "type": "string",
            "description": "上层关闭原因，仅用于控制回执；不得包含密钥或完整数据。",
            "maxLength": 256,
        },
    },
    "required": ["session_id"],
}


def _manager(ctx):
    manager = getattr(ctx, "manager", None)
    return manager if manager is not None else get_manager()


def _required_session_id(arguments: dict, tool: str):
    if not isinstance(arguments, dict) or arguments.get("session_id") is None:
        return None, Envelope(
            text="error: 'session_id' is required",
            structured=None,
            rc=1,
            error_class=None,
            graphs=[],
            meta={"tool": tool},
        )
    sid, err = _session_arg(arguments)
    if err:
        return None, invalid_session_result(tool, err)
    return sid, None


def _control_result(action: str, session_id: str, detail: dict, reason=None) -> dict:
    result = {
        "schema_version": SESSION_CONTROL_SCHEMA_VERSION,
        "executor_instance_id": executor_instance_id(),
        "action": action,
        "session_id": session_id,
        "observed_at_unix": time.time(),
        "detail": detail,
    }
    if isinstance(reason, str) and reason.strip():
        result["reason"] = reason.strip()[:256]
    return result


def _resolve_host_relative_directory(raw: object) -> tuple[Path | None, str | None]:
    if raw is None:
        raw = "."
    if not isinstance(raw, str) or not raw.strip():
        return None, "working_directory must be a non-empty relative path"
    relative = Path(raw.strip())
    if relative.is_absolute() or ".." in relative.parts:
        return None, "working_directory must stay below the MCP host root"
    root = Path.cwd().resolve()
    target = (root / relative).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None, "working_directory escapes the MCP host root"
    return target, None


@register("stata_executor_capabilities", _CAPABILITIES_SCHEMA)
def stata_executor_capabilities(arguments: dict, ctx=None) -> Envelope:
    """Return the versioned concurrency and isolation contract without starting Stata."""

    del arguments
    stats = _manager(ctx).stats()
    max_sessions = int(stats.get("max_sessions") or 0)
    structured = {
        "schema_version": EXECUTOR_CAPABILITIES_SCHEMA_VERSION,
        "executor_instance_id": executor_instance_id(),
        "execution_profile": "pystata.process-per-session.v1",
        "persistent_session": True,
        "same_session_serial": True,
        "different_sessions_parallel": True,
        "worker_process_per_session": True,
        "working_directory_binding": "immutable_per_session",
        "max_live_sessions": max_sessions or None,
        "max_parallel_commands": max_sessions or None,
        "host_process_id": os.getpid(),
    }
    return Envelope(
        text="pystata process-per-session executor; distinct sessions may run in parallel",
        structured=structured,
        rc=0,
        error_class=None,
        graphs=[],
        meta={"tool": "stata_executor_capabilities"},
    )


@register("stata_session_open", _OPEN_SCHEMA)
def stata_session_open(arguments: dict, ctx=None) -> Envelope:
    """Open and immutably bind one persistent session to a host-relative directory."""

    sid, rejected = _required_session_id(arguments, "stata_session_open")
    if rejected is not None:
        return rejected
    target, path_error = _resolve_host_relative_directory(arguments.get("working_directory"))
    if path_error is not None or target is None:
        return Envelope(
            text=f"error: {path_error}",
            structured=None,
            rc=1,
            error_class=None,
            graphs=[],
            meta={"tool": "stata_session_open"},
        )
    target.mkdir(parents=True, exist_ok=True)
    try:
        session = _manager(ctx).get_or_create(sid)
        execution = session.bind_working_directory(str(target))
    except Exception as error:
        return Envelope(
            text=f"error: {error}",
            structured=None,
            rc=1,
            error_class=None,
            graphs=[],
            meta={"tool": "stata_session_open"},
        )
    if execution.rc != 0:
        return Envelope(
            text=execution.text or "error: failed to open Stata session",
            structured=None,
            rc=execution.rc,
            error_class=None,
            graphs=[],
            meta={"tool": "stata_session_open"},
        )
    detail = session.status_snapshot()
    structured = {
        "schema_version": SESSION_OPEN_SCHEMA_VERSION,
        "executor_instance_id": executor_instance_id(),
        "session_id": sid,
        "session_generation": detail["session_generation"],
        "canonical_working_directory": str(target),
        "state": detail["state"],
    }
    return Envelope(
        text=f"session {sid}: ready",
        structured=structured,
        rc=0,
        error_class=None,
        graphs=[],
        meta={"tool": "stata_session_open"},
    )


@register("stata_session_status", _STATUS_SCHEMA)
def stata_session_status(arguments: dict, ctx=None) -> Envelope:
    """查询一个 session 的最后可观测生命周期状态；查询不会创建或启动 worker。"""
    sid, rejected = _required_session_id(arguments, "stata_session_status")
    if rejected is not None:
        return rejected
    structured = _control_result(
        "status", sid, _manager(ctx).session_status(sid)
    )
    return Envelope(
        text=f"session {sid}: {structured['detail']['state']}",
        structured=structured,
        rc=0,
        error_class=None,
        graphs=[],
        meta={"tool": "stata_session_status"},
    )


@register("stata_session_close", _CLOSE_SCHEMA)
def stata_session_close(arguments: dict, ctx=None) -> Envelope:
    """幂等关闭一个 session 及其 worker tree；不创建 session，不影响其他会话。"""
    sid, rejected = _required_session_id(arguments, "stata_session_close")
    if rejected is not None:
        return rejected
    reason = arguments.get("reason") if isinstance(arguments, dict) else None
    if reason is not None and not isinstance(reason, str):
        return Envelope(
            text="error: 'reason' must be a string",
            structured=None,
            rc=1,
            error_class=None,
            graphs=[],
            meta={"tool": "stata_session_close"},
        )
    detail = _manager(ctx).close_session(sid)
    structured = _control_result("close", sid, detail, reason)
    disposition = "closed" if detail.get("closed") else "already absent"
    return Envelope(
        text=f"session {sid}: {disposition}",
        structured=structured,
        rc=0,
        error_class=None,
        graphs=[],
        meta={"tool": "stata_session_close"},
    )
