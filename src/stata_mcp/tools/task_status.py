"""stata_task_status 工具：查询后台任务状态与结果（P5b）。"""
from __future__ import annotations

from ..envelope import Envelope
from ..output.smcl import strip_smcl
from ..tasks import DONE, ERROR, get_runner
from . import register

_STATA_TASK_STATUS_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "job_id": {
            "type": "string",
            "description": "stata_run(background=True) 返回的全局 job_id（任务表全局查询，不属某会话）。",
        }
    },
    "required": ["job_id"],
}

_EMPTY_COMMAND_FAILURE_HINTS = {
    601: "file not found",
    602: "file already exists",
    603: "file could not be opened",
}


def _command_failure_fallback(rc: int) -> str:
    hint = _EMPTY_COMMAND_FAILURE_HINTS.get(rc)
    suffix = f": {hint}" if hint else ""
    return f"Stata command failed with r({rc}){suffix}; raw output was empty"


@register("stata_task_status", _STATA_TASK_STATUS_SCHEMA)
def stata_task_status(arguments: dict, ctx=None) -> Envelope:
    """查询后台任务：running / done(带结果文本) / error。未知 job_id 报 rc=1。"""
    args = arguments if isinstance(arguments, dict) else {}
    job_id = args.get("job_id", "")

    if not isinstance(job_id, str) or not job_id.strip():
        return Envelope(
            text="error: 'job_id' is required",
            structured=None,
            rc=1,
            error_class=None,
            graphs=[],
            meta={"tool": "stata_task_status"},
        )

    runner = get_runner()
    task = runner.status(job_id)
    if task is None:
        return Envelope(
            text=f"error: unknown job_id {job_id!r}",
            structured=None,
            rc=1,
            error_class=None,
            graphs=[],
            meta={"tool": "stata_task_status", "job_id": job_id},
        )

    status = task["status"]
    if status == "running":
        return Envelope(
            text="running",
            structured=None,
            rc=0,
            error_class=None,
            graphs=[],
            meta={"tool": "stata_task_status", "job_id": job_id, "status": status},
        )
    # DONE/ERROR 走同一 formatter（P16d：删掉 ERROR 提前返回，补齐 error/elapsed/replay）。
    result = task.get("result")
    code = task.get("code", "")
    text = strip_smcl(result.text) if result else task.get("error", "")
    rc = result.rc if result else (1 if status == ERROR else 0)
    if rc != 0 and not text:
        text = _command_failure_fallback(rc)

    structured = None
    if result is not None:
        from .run import enrich_structured

        structured = enrich_structured(result.structured, code, result)

    error_class = None
    if rc != 0 and text:
        from ..output.errors import classify_error

        error_class = classify_error(rc, text)

    # 统一错误对象（command_failed / timeout / crashed / start_failed）
    error = None
    kind = result.error_kind if result is not None else ("crashed" if status == ERROR else None)
    if kind == "timeout":
        error = {"kind": "timeout", "message": text or "command was interrupted after timeout"}
    elif kind in ("crashed", "start_failed"):
        error = {"kind": kind, "message": text or "Stata session crashed"}
    elif rc != 0:
        error = {"kind": "command_failed", "rc": rc, "class": error_class, "message": text}

    meta = {"tool": "stata_task_status", "job_id": job_id, "status": status}
    if task.get("elapsed_ms") is not None:
        meta["elapsed_ms"] = task["elapsed_ms"]
    if getattr(result, "reset", False):
        meta["session_reset"] = True
    if getattr(result, "replay", None):
        cmds = [e.get("cmd") for e in result.replay if e.get("cmd")]
        meta["replay"] = cmds[-30:]
        meta["replay_full"] = len(cmds)
    metadata = dict(task.get("metadata") or {})
    artifact_contracts = list(metadata.get("artifact_contracts") or [])
    if result is not None and artifact_contracts:
        from .run import _capture_artifact_outputs

        captured_outputs, missing_outputs = _capture_artifact_outputs(
            artifact_contracts,
            command_hash=getattr(result, "command_hash", None),
            operation_attempt_id=metadata.get("operation_attempt_id"),
        )
        if captured_outputs:
            meta["artifact_outputs"] = captured_outputs
        if missing_outputs:
            meta["artifact_output_contract_status"] = "missing_required"
            meta["missing_required_artifact_outputs"] = missing_outputs
        else:
            meta["artifact_output_contract_status"] = "complete"

    execution_receipt = None
    if result is not None:
        from .run import build_execution_receipt

        execution_receipt = build_execution_receipt(
            task.get("session"),
            result,
            truncated=False,
            structured_result_status="complete" if structured is not None else None,
        )

    return Envelope(
        text=text or ("(task finished with empty output)" if status == DONE else "error"),
        structured=structured,
        rc=rc,
        error_class=error_class,
        graphs=[],
        meta=meta,
        error=error,
        execution_receipt=execution_receipt,
    )
