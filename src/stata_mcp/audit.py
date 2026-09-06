"""结构化审计日志（P1c）：记录每次工具调用的关键事实，供排障/合规/分析。

设计：
- JSON Lines 写入 ``~/.statamcp/audit.jsonl``（用户级目录，可配 STATAMCP_AUDIT）；
- 每条：时间、工具名、关键参数（含 code 的 SHA 而非原文——命令原文可能在
  audit 里敏感，哈希可对账又不过度暴露）、rc、error_class、elapsed_ms、
  command_hash、是否 truncated/reset；
- 写入走线程锁（server 是多线程的），失败静默（审计不能拖垮主流程）。
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time

_AUDIT_LOCK = threading.Lock()
# 环境变量覆盖审计文件路径
_AUDIT_PATH = os.environ.get(
    "STATAMCP_AUDIT", os.path.join(os.path.expanduser("~"), ".statamcp", "audit.jsonl")
)


def _audit_dir() -> str:
    return os.path.dirname(_AUDIT_PATH)


def record(
    *,
    tool: str,
    code: str | None = None,
    rc: int,
    elapsed_ms: float,
    error_class: str | None = None,
    error_kind: str | None = None,
    truncated: bool = False,
    reset: bool = False,
    extra: dict | None = None,
) -> None:
    """记一条工具调用审计。code 只存哈希（前 16 位），不存原文。"""
    entry = {
        "ts": time.time(),
        "tool": tool,
        "rc": rc,
        "elapsed_ms": round(elapsed_ms, 3),
        "error_class": error_class,
        "error_kind": error_kind,
        "truncated": truncated,
        "reset": reset,
    }
    if code:
        entry["code_hash"] = hashlib.sha256(code.encode("utf-8")).hexdigest()[:16]
    if extra:
        entry.update(extra)
    line = json.dumps(entry, ensure_ascii=False)

    with _AUDIT_LOCK:
        try:
            os.makedirs(_audit_dir(), exist_ok=True)
            with open(_AUDIT_PATH, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass  # 审计失败不影响主流程
