"""stata_session_history 工具：读当前会话的命令日志（P15）。

日志记录在**主进程** Session（不是 worker）——worker 崩溃/会话重置后日志仍在，
agent 可据此重放命令恢复状态，或追溯"这个会话跑过什么"。
"""
from __future__ import annotations

from ..envelope import Envelope
from . import register
from .run import _resolve_backend, _session_arg, invalid_session_result

_STATA_SESSION_HISTORY_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "session_id": {
            "type": "string",
            "description": "会话标识；省略用 'default'。",
        },
        "last": {
            "type": "integer",
            "description": "只返回最近 N 条（默认全部，上限 100）。",
            "minimum": 1,
            "maximum": 100,
        }
    },
            "required": [],
}


@register("stata_session_history", _STATA_SESSION_HISTORY_SCHEMA)
def stata_session_history(arguments: dict, ctx=None) -> Envelope:
    """返回当前会话的命令日志（seq/cmd/rc），供追溯或崩溃后重放。"""
    args = arguments if isinstance(arguments, dict) else {}
    last = args.get("last", None)
    meta = {"tool": "stata_session_history"}

    sid, sid_err = _session_arg(args)

    if sid_err:

        return invalid_session_result("stata_session_history", sid_err)

    session = _resolve_backend(ctx, sid)
    journal = session.journal()
    if isinstance(last, int) and not isinstance(last, bool) and last > 0:
        journal = journal[-last:]

    cmds = [e["cmd"] for e in journal]
    n = len(cmds)
    replay_do = "\n".join(cmds)

    return Envelope(
        text=f"{n} command(s) recorded; 'structured.replay_do' re-runs them to rebuild state"
        if cmds else "(no commands recorded in this session yet)",
        structured={
            "count": n,
            "journal": journal,
            "replay_do": replay_do,
        },
        rc=0,
        error_class=None,
        graphs=[],
        meta=meta,
    )
