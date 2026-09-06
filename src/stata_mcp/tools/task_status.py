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
            "description": "stata_run(background=True) 返回的 job_id。",
        }
    },
            "session_id": {
            "type": "string",
            "description": "会话标识；省略用 'default'。",
        },
"required": ["job_id"],
}


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
    if status == ERROR:
        return Envelope(
            text=f"error: {task.get('error', 'unknown error')}",
            structured=None,
            rc=1,
            error_class=None,
            graphs=[],
            meta={"tool": "stata_task_status", "job_id": job_id, "status": status},
        )

    # DONE：附结果文本 + rc + 结构化 + provenance（P16 修复：后台结果此前丢结构化）
    result = task.get("result")
    code = task.get("code", "")
    text = strip_smcl(result.text) if result else ""
    structured = None
    if result is not None and getattr(result, "structured", None):
        from .run import enrich_structured

        structured = enrich_structured(result.structured, code, result)
    return Envelope(
        text=text or "(task finished with empty output)",
        structured=structured,
        rc=result.rc if result else 0,
        error_class=None,
        graphs=[],
        meta={"tool": "stata_task_status", "job_id": job_id, "status": status},
    )
