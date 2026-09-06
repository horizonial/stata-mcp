"""后台任务执行：让长命令不阻塞 MCP 调用，可轮询状态、可打断（P5b）。

为什么需要它：MCP 客户端通常串行调工具——若 ``stata_run`` 同步阻塞在一条
跑几分钟的 bootstrap 上，agent 既拿不到返回、也发不出"取消"。后台执行让
``stata_run(background=True)`` 立即返回 job_id，命令在独立线程里跑（仍受
backend 的串行锁约束），agent 再用 ``stata_task_status`` 轮询、``stata_break``
打断。

线程模型（P5b 实测定案）：
- 后台线程跑 ``backend.execute``（内部持 backend 的锁，阻塞在 Stata）；
- 打断走 ``backend.interrupt()``（**不持锁**，直接调 StataSO_SetBreak），
  打断的正是后台线程里正在跑的 Stata 命令，不会死锁。
"""
from __future__ import annotations

import threading
import time
import uuid

from .session import get_manager

# 任务状态常量
RUNNING = "running"
DONE = "done"
ERROR = "error"

# 任务表生命周期（P16c #3 / P16d）：完成的记录保留上限与 TTL，防长期堆积。
_DONE_TTL = 3600.0  # 完成(含失败)记录 1h 后清理
_MAX_KEPT = 200  # 最多保留的完成记录数（超出清最旧）
_MAX_ACTIVE = 64  # 运行中任务上限（每个任务一个后台线程/一个 Stata 会话占用）
_MAX_TOTAL = 1024  # 任务表总上限（含 running），防无限提交


class TaskCapacityExceeded(RuntimeError):
    """达到任务并发/总量上限。"""


class TaskRunner:
    """后台任务表：job_id -> 状态/结果。P10 起用 Session（worker 子进程）。

    P16d：真正惰性（初始化不预建 default 会话）；提交时才解析 session；运行上限 +
    总量上限；status/snapshot 前也清理过期。
    """

    def __init__(self, session=None) -> None:
        self._session = session  # None = 惰性，submit 时按 session_id/default 再取
        self._tasks: dict[str, dict] = {}
        self._lock = threading.Lock()

    def submit(self, code: str, session_id: str | None = None) -> str:
        """提交后台执行，返回 job_id。session_id 缺省用 default（惰性解析）。"""
        # 运行中(含排队)任务上限
        active = sum(1 for t in self._tasks.values() if t.get("status") == RUNNING)
        if active >= _MAX_ACTIVE:
            raise TaskCapacityExceeded(
                f"too many active background tasks ({active} >= {_MAX_ACTIVE})"
            )
        self._prune()
        with self._lock:
            if len(self._tasks) >= _MAX_TOTAL:
                raise TaskCapacityExceeded(
                    f"task table full ({_MAX_TOTAL}); retry after TTL cleanup"
                )
            if session_id is not None:
                session = get_manager().get_or_create(session_id)
            elif self._session is not None:
                session = self._session
            else:
                session = get_manager().get_or_create("default")
            job_id = uuid.uuid4().hex[:12]
            self._tasks[job_id] = {
                "status": RUNNING, "result": None, "code": code, "session": session,
                "submitted": time.time(),
            }
        threading.Thread(
            target=self._run, args=(job_id, code), daemon=True
        ).start()
        return job_id

    def _run(self, job_id: str, code: str) -> None:
        session = self._tasks.get(job_id, {}).get("session", self._session)
        t0 = time.time()
        try:
            result = session.execute(code)
            with self._lock:
                self._tasks[job_id] = {
                    "status": DONE, "result": result, "code": code,
                    "submitted": self._tasks[job_id].get("submitted", t0),
                    "finished": time.time(), "elapsed_ms": round((time.time() - t0) * 1000, 3),
                }
        except Exception as exc:  # 引擎层异常（极少数逃过 capture 的）
            with self._lock:
                self._tasks[job_id] = {
                    "status": ERROR,
                    "error": f"{type(exc).__name__}: {exc}",
                    "code": code,
                    "submitted": self._tasks[job_id].get("submitted", t0),
                    "finished": time.time(), "elapsed_ms": round((time.time() - t0) * 1000, 3),
                }

    def _prune(self) -> None:
        """清理过期完成记录 + 超出 _MAX_KEPT/_MAX_TOTAL。submit/status/snapshot 都调用。"""
        now = time.time()
        with self._lock:
            for jid in list(self._tasks):
                t = self._tasks[jid]
                if t.get("status") != RUNNING and now - t.get("finished", now) > _DONE_TTL:
                    del self._tasks[jid]
            done = [jid for jid, t in self._tasks.items() if t.get("status") != RUNNING]
            if len(done) > _MAX_KEPT:
                for jid in sorted(done, key=lambda j: self._tasks[j].get("finished", 0))[
                    : len(done) - _MAX_KEPT
                ]:
                    del self._tasks[jid]

    def status(self, job_id: str) -> dict | None:
        """查任务状态；未知 job_id 返回 None。读前先清理过期（P16d）。"""
        self._prune()
        with self._lock:
            task = self._tasks.get(job_id)
            return dict(task) if task else None

    def snapshot(self) -> dict[str, str]:
        """所有任务的状态快照（job_id -> status），供 list 用。读前先清理过期。"""
        self._prune()
        with self._lock:
            return {jid: t["status"] for jid, t in self._tasks.items()}


# 进程内单例（与 get_backend 单例对齐）
_runner: TaskRunner | None = None


def get_runner() -> TaskRunner:
    global _runner
    if _runner is None:
        _runner = TaskRunner()
    return _runner
