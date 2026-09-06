"""Session / SessionManager：会话隔离 + 懒启动 + 空闲回收 + 自愈（P10a）。

相对 mcp-stata（每 session 常驻一子进程）与 stata-mcp（预起 worker 池）的提升：
- **懒启动**：worker 第一次 execute 才 spawn+init，不占 license 直到真正执行；
- **空闲回收**：会话空闲超时自动 close，释放 license 席位；
- **分层自愈**：execute 超时先 break（保状态）→ 仍卡则 kill worker 重建（丢状态，
  但返回明确的 reset 信号）；worker C 崩溃（进程死）→ 下次 execute 自动重建。

主进程通过 Queue 与 worker 通信；结构化结果由 worker 内完成（见 worker.py）。
"""
from __future__ import annotations

import multiprocessing as mp
import queue
import threading
import time
from dataclasses import dataclass, field

from .stata.worker import worker_main

# 默认超时：单条命令执行上限（秒）、break 后宽限、空闲回收阈值
_DEFAULT_EXEC_TIMEOUT = 300.0
_BREAK_GRACE = 3.0
_DEFAULT_IDLE_TIMEOUT = 600.0


@dataclass
class SessionResult:
    """session.execute 的返回：text/rc/结构化 + reset 标记 + 溯源元数据。"""

    text: str
    rc: int
    e_changed: bool = False
    structured: dict | None = None
    reset: bool = False  # 本次执行期间会话被重建（内存数据已丢失）
    command_hash: str | None = None  # 命令代码哈希（溯源到哪条命令）
    data_signature: str | None = None  # 数据指纹（溯源到哪个数据版本）
    error_kind: str | None = None  # "timeout" / "crashed" / None（引擎级错误托底）
    data_load_cmd: str | None = None  # 载入数据命令（do-file 往返前缀，P0-4）
    exec_seq: int | None = None  # 会话内执行序号（结果版本化，P0-4）
    replay: list[dict] | None = None  # 崩溃重置时返回的命令日志（供 agent 重放，P15）


class _SessionDead(RuntimeError):
    """worker 进程死亡（C 崩溃 / 被 kill），无法继续。"""


class Session:
    """一个 Stata 会话 = 一个懒启动的 worker 子进程。"""

    def __init__(self, session_id: str, idle_timeout: float = _DEFAULT_IDLE_TIMEOUT):
        self.id = session_id
        self._idle_timeout = idle_timeout
        self._request_q: mp.Queue | None = None
        self._response_q: mp.Queue | None = None
        self._break_q: mp.Queue | None = None
        self._proc: mp.Process | None = None
        self._lock = threading.Lock()
        self._msg_id = 0
        self._last_used = time.time()
        self._job = None  # Windows Job Object（P14 父死子亡）
        # 会话命令日志（P15）：放在主进程而不是 worker——worker 崩溃时主进程还活着，
        # 日志才保得住。环形上限 _JOURNAL_MAX，reset 时随结果返回供 agent 重放。
        self._journal: list[dict] = []
        self._journal_max = 500

    # ---- 生命周期 -----------------------------------------------------------

    def _start(self) -> None:
        import os

        ctx = mp.get_context("spawn")
        self._request_q = ctx.Queue()
        self._response_q = ctx.Queue()
        self._break_q = ctx.Queue()
        self._proc = ctx.Process(
            target=worker_main,
            # parent_pid：孤儿看门狗（P14 兜底），主进程死则 worker 自退释放 license。
            args=(self._request_q, self._response_q, self._break_q, os.getpid()),
            daemon=True,
        )
        self._proc.start()
        # Windows Job Object（P14 主机制）：父进程无论怎么死，OS 关闭 job 句柄时
        # 自动终止 worker。不依赖 PID 轮询（Windows PID 复用会让轮询失效）。
        try:
            if os.name == "nt":
                from .platform.job import create

                self._job = create()
                if self._job is not None:
                    self._job.assign(self._proc.pid)
        except Exception:
            self._job = None

    def _ensure_started(self) -> bool:
        """确保 worker 存活；返回 True 表示"之前的 worker 死了、本次是重建"。

        区分首次懒启动（_proc 为 None，不算 reset）与崩溃重建（is_alive 为
        False，算 reset）：reset 只表示"内存数据丢失"，首次启动没有可丢的数据。
        """
        if self._proc is None:
            self._start()
            return False
        if not self._proc.is_alive():
            # worker 进程已死（C 崩溃等）：清理并重建
            self._cleanup()
            self._start()
            return True
        return False

    def _cleanup(self) -> None:
        if self._proc is not None:
            if self._proc.is_alive():
                self._proc.terminate()
            self._proc.join(timeout=2)
        self._proc = None
        self._request_q = self._response_q = self._break_q = None
        if self._job is not None:
            try:
                self._job.close()  # 关闭句柄（此时 worker 已终止，无副作用）
            except Exception:
                pass
            self._job = None

    def is_alive(self) -> bool:
        # 从未启动（懒会话，_proc 为 None）视为"活着"——还能用，只是还没点火。
        # 只有"启动过且进程死了"才是真崩溃。
        return self._proc is None or self._proc.is_alive()

    def is_crashed(self) -> bool:
        """启动过但进程已死（C 引擎崩溃 / 被 kill）。"""
        return self._proc is not None and not self._proc.is_alive()

    def is_idle(self) -> bool:
        return time.time() - self._last_used > self._idle_timeout

    def close(self) -> None:
        with self._lock:
            if self._proc is not None and self._proc.is_alive():
                try:
                    self._request_q.put({"type": "close"})
                    self._proc.join(timeout=3)
                except Exception:
                    pass
            self._cleanup()

    # ---- 执行 ---------------------------------------------------------------

    def execute(self, code: str, timeout: float = _DEFAULT_EXEC_TIMEOUT) -> SessionResult:
        with self._lock:
            reset = self._ensure_started()
            self._last_used = time.time()
            self._msg_id += 1
            mid = self._msg_id

            self._request_q.put({"id": mid, "type": "execute", "code": code})
            interrupted = False
            try:
                resp = self._response_q.get(timeout=timeout)
            except queue.Empty:
                # 超时：先 break 保状态
                self._request_break()
                try:
                    resp = self._response_q.get(timeout=_BREAK_GRACE)
                    interrupted = True  # break 生效，命令被超时打断（保状态）
                except queue.Empty:
                    # break 后仍卡死：杀 worker，返回 reset + 历史日志（供重放）
                    self._cleanup()
                    return self._make_reset(
                        f"(command timed out after {timeout}s and was killed; "
                        f"session reset; command: {code[:120]})",
                        "timeout",
                    )
            except (EOFError, OSError, ValueError):
                # worker 在执行中崩溃（C 引擎 crash 等）→ reset + 历史日志
                self._cleanup()
                return self._make_reset(
                    f"(session crashed while running command: {code[:120]}; session reset)",
                    "crashed",
                )

            if resp.get("id") != mid:
                return self._make_reset("(session response mismatch; reset)", "crashed")

            rc = resp.get("rc", 0)
            self._log(mid, code, rc)  # P15：命令日志（主进程内存，worker 崩溃也在）

            # reset=True 表示本次是"重建后的新 worker"执行的：之前 worker 上跑的
            # 命令效果全丢，附上完整日志供 agent 重放恢复（P15）。
            replay = self.journal() if reset else None
            return SessionResult(
                text=resp.get("text", ""),
                rc=rc,
                e_changed=resp.get("e_changed", False),
                structured=resp.get("structured"),
                reset=reset,
                command_hash=resp.get("command_hash"),
                data_signature=resp.get("data_signature"),
                error_kind="timeout" if interrupted else None,
                data_load_cmd=resp.get("data_load_cmd"),
                exec_seq=resp.get("exec_seq"),
                replay=replay,
            )

    def _make_reset(self, text: str, kind: str) -> SessionResult:
        """reset 结果：附带崩溃前命令日志（replay 原料，供 agent 重放恢复状态）。"""
        return SessionResult(
            text=text, rc=601, reset=True, error_kind=kind, replay=self.journal()
        )

    def _log(self, seq: int, code: str, rc: int) -> None:
        """追加一条命令日志（环形，上限 _journal_max）。"""
        self._journal.append({"seq": seq, "cmd": code, "rc": rc})
        if len(self._journal) > self._journal_max:
            self._journal.pop(0)

    def journal(self) -> list[dict]:
        """当前会话命令日志副本（seq/cmd/rc）。worker 崩溃、会话重置后仍保留。"""
        with self._lock:
            return [dict(e) for e in self._journal]

    def clear_journal(self) -> None:
        with self._lock:
            self._journal.clear()

    def interrupt(self) -> None:
        """打断当前正在执行的命令（走独立 break 通道）。"""
        if self._proc is not None and self._proc.is_alive():
            self._request_break()

    def preview(self, n: int = 10) -> dict:
        """读当前数据集前 n 行（P1b data_rows），worker 内完成。"""
        with self._lock:
            self._ensure_started()
            self._last_used = time.time()
            self._msg_id += 1
            mid = self._msg_id
            self._request_q.put({"id": mid, "type": "rows", "n": int(n)})
            try:
                resp = self._response_q.get(timeout=_DEFAULT_EXEC_TIMEOUT)
            except (queue.Empty, EOFError, OSError, ValueError):
                self._cleanup()
                return {}
            return resp

    def _request_break(self) -> None:
        try:
            self._break_q.put(True)
        except Exception:
            pass

    def snapshot(self) -> dict:
        """只读当前会话状态（e()/r()/shape），由 worker 内完成结构化读取。"""
        with self._lock:
            reset = self._ensure_started()
            self._last_used = time.time()
            self._msg_id += 1
            mid = self._msg_id
            self._request_q.put({"id": mid, "type": "snapshot"})
            try:
                resp = self._response_q.get(timeout=_DEFAULT_EXEC_TIMEOUT)
            except queue.Empty:
                self._cleanup()
                return {"reset": True}
            except (EOFError, OSError, ValueError):
                self._cleanup()
                return {"reset": True}
            if reset:
                resp["reset"] = True
            return resp


class SessionLimitExceeded(RuntimeError):
    """达到最大会话数（license 席位耗尽）。"""


class SessionManager:
    """管理多个会话：按 session_id 懒创建 + 空闲回收 + 自愈 + 上限（P0-3）。"""

    def __init__(
        self,
        idle_timeout: float = _DEFAULT_IDLE_TIMEOUT,
        max_sessions: int = 0,
    ):
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()
        self._idle_timeout = idle_timeout
        self._max_sessions = int(max_sessions or 0)

    def set_max_sessions(self, n: int) -> None:
        self._max_sessions = int(n or 0)

    def get_or_create(self, session_id: str = "default") -> Session:
        with self._lock:
            self._reap_idle()
            s = self._sessions.get(session_id)
            if s is not None and s.is_alive():
                return s

            # 需要新建会话：先检查上限（每个会话 = 一个 license 席位）。
            if self._max_sessions > 0 and len(self._sessions) >= self._max_sessions:
                raise SessionLimitExceeded(
                    f"session limit reached ({self._max_sessions} live sessions = "
                    f"{self._max_sessions} Stata license seats); close an idle session "
                    "or raise security.max_sessions"
                )

            if s is not None:
                s.close()  # 死会话清理
            s = Session(session_id, idle_timeout=self._idle_timeout)
            self._sessions[session_id] = s
            return s

    def _reap_idle(self) -> None:
        """回收空闲会话 + 清理崩溃会话（启动过但进程死了），防 license 泄漏。"""
        for sid, s in list(self._sessions.items()):
            if s.is_idle() or s.is_crashed():
                s.close()
                del self._sessions[sid]

    def stats(self) -> dict:
        """会话状态快照（诊断用）。"""
        with self._lock:
            live = sum(1 for s in self._sessions.values() if s.is_alive())
            return {
                "live": live,
                "total_tracked": len(self._sessions),
                "max_sessions": self._max_sessions,
            }

    def close_all(self) -> None:
        with self._lock:
            for s in self._sessions.values():
                s.close()
            self._sessions.clear()


# 进程内单例（与 get_backend 单例对齐）
_manager: SessionManager | None = None


def get_manager() -> SessionManager:
    global _manager
    if _manager is None:
        _manager = SessionManager()
    return _manager
