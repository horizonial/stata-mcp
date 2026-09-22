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

    def submit(
        self,
        code: str,
        session_id: str | None = None,
        timeout: float | None = None,
        metadata: dict | None = None,
        session=None,
    ) -> str:
        """提交后台执行，返回 job_id。session_id 缺省用 default（惰性解析）。

        P16e #3：prune + active/total 上限检查 + 登记 在**同一个锁域**内完成，
        消除并发提交时 active 计数竞态。
        """
        with self._lock:
            self._prune_locked()
            active = sum(1 for t in self._tasks.values() if t.get("status") == RUNNING)
            if active >= _MAX_ACTIVE:
                raise TaskCapacityExceeded(
                    f"too many active background tasks ({active} >= {_MAX_ACTIVE})"
                )
            if len(self._tasks) >= _MAX_TOTAL:
                raise TaskCapacityExceeded(
                    f"task table full ({_MAX_TOTAL}); retry after TTL cleanup"
                )
            selected_session = session
            if selected_session is None and session_id is not None:
                selected_session = get_manager().get_or_create(session_id)
            elif selected_session is None and self._session is not None:
                selected_session = self._session
            elif selected_session is None:
                selected_session = get_manager().get_or_create("default")
            job_id = uuid.uuid4().hex[:12]
            self._tasks[job_id] = {
                "status": RUNNING,
                "result": None,
                "code": code,
                "session": selected_session,
                "submitted": time.time(), "timeout": timeout,
                "metadata": dict(metadata or {}),
            }
        threading.Thread(
            target=self._run, args=(job_id, code, timeout), daemon=True
        ).start()
        return job_id

    def _run(self, job_id: str, code: str, timeout: float | None = None) -> None:
        session = self._tasks.get(job_id, {}).get("session", self._session)
        t0 = time.time()
        try:
            result = (
                session.execute(code)
                if timeout is None
                else session.execute(code, timeout=timeout)
            )
            with self._lock:
                self._tasks[job_id] = {
                    "status": DONE, "result": result, "code": code,
                    "session": session,
                    "metadata": dict(self._tasks[job_id].get("metadata") or {}),
                    "submitted": self._tasks[job_id].get("submitted", t0),
                    "finished": time.time(), "elapsed_ms": round((time.time() - t0) * 1000, 3),
                }
        except Exception as exc:  # 引擎层异常（极少数逃过 capture 的）
            with self._lock:
                self._tasks[job_id] = {
                    "status": ERROR,
                    "error": f"{type(exc).__name__}: {exc}",
                    "code": code,
                    "metadata": dict(self._tasks[job_id].get("metadata") or {}),
                    "submitted": self._tasks[job_id].get("submitted", t0),
                    "finished": time.time(), "elapsed_ms": round((time.time() - t0) * 1000, 3),
                }

    def _prune_locked(self) -> None:
        """清理过期/超量记录。**调用方须已持有 self._lock**（P16e #3 单锁域）。"""
        now = time.time()
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
        with self._lock:
            self._prune_locked()
            task = self._tasks.get(job_id)
            return dict(task) if task else None

    def snapshot(self) -> dict[str, str]:
        """所有任务的状态快照（job_id -> status），供 list 用。读前先清理过期。"""
        with self._lock:
            self._prune_locked()
            return {jid: t["status"] for jid, t in self._tasks.items()}


# 进程内单例（与 get_manager 单例对齐）
_runner: TaskRunner | None = None
_runner_lock = threading.Lock()


def get_runner() -> TaskRunner:
    """返回单例 TaskRunner（P16e #4：懒初始化加锁，防并发建多个 runner 导致
    job 登记到旧实例、status 查不到）。"""
    global _runner
    if _runner is None:
        with _runner_lock:
            if _runner is None:
                _runner = TaskRunner()
    return _runner
