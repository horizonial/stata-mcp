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
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .stata.worker import worker_main

# 默认超时：单条命令执行上限（秒）、break 后宽限、空闲回收阈值
_DEFAULT_EXEC_TIMEOUT = 300.0
_BREAK_GRACE = 3.0
_DEFAULT_IDLE_TIMEOUT = 600.0
# 启动握手超时（P16 #3）：worker 点火 + 引擎 init/license 的等待上限。
# 超过即判"启动失败"，快速返回，不干等 _DEFAULT_EXEC_TIMEOUT。
# P16b：60→20s 更贴近 fail-fast（引擎冷启动 + license 网络认证一般 <10s）。
_START_TIMEOUT = 20.0
# 只读操作（snapshot/preview）超时：不跑命令，理应很快，别给 300s。
_READ_TIMEOUT = 60.0
# generation 只在一个 MCP 进程内单调；进程重启后必须用新的 executor identity
# 消除 (session_id, generation) 重复的歧义。
_EXECUTOR_INSTANCE_ID = str(uuid.uuid4())
_GENERATION_LOCK = threading.Lock()
_GENERATION_COUNTER = 0


def _next_session_generation() -> int:
    """分配本 MCP 实例内唯一的 worker generation。

    generation 不能只在 ``Session`` 对象内从 1 开始：显式关闭并用同一个
    session_id 重建后会产生重复身份。全实例单调序号与 executor_instance_id
    组合后，能够稳定标识一次具体的 Stata worker 生命周期。
    """
    global _GENERATION_COUNTER
    with _GENERATION_LOCK:
        _GENERATION_COUNTER += 1
        return _GENERATION_COUNTER


def executor_instance_id() -> str:
    """返回本 MCP 进程稳定的执行器身份。"""
    return _EXECUTOR_INSTANCE_ID


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
    session_id: str | None = None
    session_generation: int | None = None
    structured_result_status: str | None = None
    runtime_environment: dict | None = None
    supervision_proof: dict | None = None
    executor_instance_id: str | None = None
    return_state: dict | None = None


class _WorkerDead(RuntimeError):
    """worker 进程死亡（C 崩溃 / 被 kill）。用于 fail-fast（P16b）。"""


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
        # 显式 session close 必须能终止正在执行的 worker，不能等待 execute 锁。
        # lifecycle lock 只保护进程/队列句柄的替换与清理，不保护 Stata 命令串行性。
        self._lifecycle_lock = threading.RLock()
        self._binding_lock = threading.Lock()
        self._msg_id = 0
        self._last_used = time.time()
        self._job = None  # Windows Job Object（P14 父死子亡）
        # 会话命令日志（P15）：放在主进程而不是 worker——worker 崩溃时主进程还活着，
        # 日志才保得住。环形上限 _JOURNAL_MAX，reset 时随结果返回供 agent 重放。
        self._journal: list[dict] = []
        self._journal_max = 500
        # 启动握手状态（P16 #3）：worker 发 ready 前不认为可执行
        self._ready_ok = False
        # 每次创建新 worker 都产生新 generation。它是 session 内单调身份，不能
        # 用 exec_seq 代替：worker 重建后 exec_seq 会从 1 重新开始。
        self._generation = 0
        self._runtime_environment: dict | None = None
        self._retired = False
        self._working_directory: str | None = None
        self._working_directory_result: SessionResult | None = None
        # Stata's ST_*.tmp names are not globally collision-resistant.  A
        # private directory per PyStata worker prevents parallel or previously
        # crashed sessions from colliding with r(602) on Windows.
        self._worker_temp_directory: Path | None = None

    # ---- 生命周期 -----------------------------------------------------------

    def _start(self) -> None:
        import os

        with self._lifecycle_lock:
            if self._retired:
                raise _SessionDead("session was explicitly closed")
            self._generation = _next_session_generation()
            ctx = mp.get_context("spawn")
            self._request_q = ctx.Queue()
            self._response_q = ctx.Queue()
            self._break_q = ctx.Queue()
            self._worker_temp_directory = Path(
                tempfile.mkdtemp(prefix="stata-mcp-worker-")
            ).resolve()
            self._proc = ctx.Process(
                target=worker_main,
                # parent_pid：孤儿看门狗（P14 兜底），主进程死则 worker 自退释放 license。
                args=(
                    self._request_q,
                    self._response_q,
                    self._break_q,
                    os.getpid(),
                    str(self._worker_temp_directory),
                ),
                daemon=True,
            )
            try:
                self._proc.start()
            except Exception:
                shutil.rmtree(self._worker_temp_directory, ignore_errors=True)
                self._worker_temp_directory = None
                raise
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
        with self._lifecycle_lock:
            proc = self._proc
            job = self._job
            # 先撤销公开句柄，让正在 _recv 的执行线程确定性观察到 worker 已失效。
            self._proc = None
            self._request_q = self._response_q = self._break_q = None
            self._ready_ok = False  # 进程换了，ready 状态必须重置（P16 #3）
            self._runtime_environment = None
            self._job = None
            worker_temp_directory = self._worker_temp_directory
            self._worker_temp_directory = None
            if proc is not None:
                if proc.is_alive():
                    proc.terminate()
                proc.join(timeout=2)
            if job is not None:
                try:
                    job.close()  # 关闭句柄（此时 worker 已终止，无副作用）
                except Exception:
                    pass
            if worker_temp_directory is not None:
                shutil.rmtree(worker_temp_directory, ignore_errors=True)

    def is_alive(self) -> bool:
        # 从未启动（懒会话，_proc 为 None）视为"活着"——还能用，只是还没点火。
        # 只有"启动过且进程死了"才是真崩溃。
        return not self._retired and (self._proc is None or self._proc.is_alive())

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

    def force_close(self) -> dict:
        """立即终止本 session，不等待正在执行的命令释放串行锁。

        这是暂停/恢复/tainted-session 清理所需的控制边界。正在执行的
        ``execute`` 会在下一次轮询时得到 ``_WorkerDead``，并形成 crashed receipt；
        本方法本身不启动新 worker，也不清空历史 journal。
        """
        before = self.status_snapshot()
        with self._lifecycle_lock:
            self._retired = True
        self._cleanup()
        return {"before": before, "after": self.status_snapshot()}

    def status_snapshot(self) -> dict:
        """无副作用、非阻塞地返回 MCP 对 session 生命周期的直接观察。"""
        with self._lifecycle_lock:
            proc = self._proc
            retired = self._retired
            started = proc is not None
            alive = bool(proc is not None and proc.is_alive())
            ready = bool(self._ready_ok and alive)
            if retired:
                state = "closed"
            elif not started:
                state = "lazy"
            elif not alive:
                state = "crashed"
            elif ready:
                state = "ready"
            else:
                state = "starting"
            return {
                "session_id": self.id,
                "session_generation": self._generation or None,
                "state": state,
                "worker_started": started,
                "worker_alive_observed": alive,
                "worker_ready_acknowledged": ready,
                "worker_pid": getattr(proc, "pid", None),
                "operation_in_flight": self._lock.locked(),
                "windows_job_object_attached": self._job is not None,
                "last_used_at_unix": self._last_used,
                "canonical_working_directory": self._working_directory,
            }

    def bind_working_directory(
        self, working_directory: str, *, timeout: float = 30.0
    ) -> SessionResult:
        """Bind this session once to one canonical directory.

        The binding is a lifecycle property, not a general research command.  Reopening the
        same path is idempotent; rebinding an existing session is rejected so one session
        identity cannot silently move between Workspace scopes.
        """

        canonical = str(Path(working_directory).resolve())
        with self._binding_lock:
            if self._working_directory is not None:
                if self._working_directory != canonical:
                    raise ValueError("session is already bound to another working directory")
                if self._working_directory_result is None:
                    raise RuntimeError("session working-directory receipt is unavailable")
                return self._working_directory_result
            result = self.execute(f'cd "{Path(canonical).as_posix()}"', timeout=timeout)
            if result.rc == 0:
                self._working_directory = canonical
                self._working_directory_result = result
            return result

    @property
    def canonical_working_directory(self) -> str | None:
        """Return the immutable session directory without starting the worker."""
        with self._binding_lock:
            return self._working_directory

    # ---- 执行 ---------------------------------------------------------------

    def execute(self, code: str, timeout: float = _DEFAULT_EXEC_TIMEOUT) -> SessionResult:
        with self._lock:
            try:
                reset = self._ensure_started()
            except _SessionDead:
                return self._make_reset("(session was explicitly closed)", "crashed")
            # P16 #3：启动握手——worker 点火失败/超时则快速返回，不干等命令超时。
            if not self._await_ready():
                return self._make_reset(
                    f"(session failed to start within {_START_TIMEOUT}s "
                    "(Stata engine init/license problem)); session reset",
                    "start_failed",
                )
            self._last_used = time.time()
            self._msg_id += 1
            mid = self._msg_id

            self._request_q.put({"id": mid, "type": "execute", "code": code})
            interrupted = False
            try:
                resp = self._recv(timeout)
            except _WorkerDead:
                # worker 在执行中崩溃（C 引擎 crash 等）→ fail-fast（P16b）
                self._cleanup()
                return self._make_reset(
                    f"(session crashed while running command: {code[:120]}; session reset)",
                    "crashed",
                )
            except queue.Empty:
                # 超时：先 break 保状态
                self._request_break()
                try:
                    resp = self._recv(_BREAK_GRACE)
                    interrupted = True  # break 生效，命令被超时打断（保状态）
                except _WorkerDead:
                    self._cleanup()
                    return self._make_reset(
                        f"(session crashed while running command: {code[:120]}; session reset)",
                        "crashed",
                    )
                except queue.Empty:
                    # break 后仍卡死：杀 worker，返回 reset + 历史日志（供重放）
                    self._cleanup()
                    return self._make_reset(
                        f"(command timed out after {timeout}s and was killed; "
                        f"session reset; command: {code[:120]})",
                        "timeout",
                    )

            if resp.get("id") != mid:
                return self._make_reset("(session response mismatch; reset)", "crashed")

            rc = resp.get("rc", 0)
            self._log(mid, code, rc)  # P15：命令日志（主进程内存，worker 崩溃也在）

            # reset=True 表示本次是"重建后的新 worker"执行的：之前 worker 上跑的
            # 命令效果全丢，附上完整日志供 agent 重放恢复（P15）。
            replay = self._journal_snapshot() if reset else None
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
                session_id=self.id,
                session_generation=self._generation,
                structured_result_status=resp.get("structured_result_status"),
                runtime_environment=dict(self._runtime_environment or {}),
                supervision_proof=self._supervision_proof(),
                executor_instance_id=_EXECUTOR_INSTANCE_ID,
                return_state=dict(resp.get("return_state") or {}),
            )

    def _make_reset(self, text: str, kind: str) -> SessionResult:
        """reset 结果：附带崩溃前命令日志（replay 原料，供 agent 重放恢复状态）。

        在 execute 锁内调用，用 _journal_snapshot 而非 journal()（后者会二次加锁死锁）。
        """
        return SessionResult(
            text=text, rc=601, reset=True, error_kind=kind,
            replay=self._journal_snapshot(),
            session_id=self.id,
            session_generation=self._generation,
            structured_result_status="not_applicable",
            runtime_environment=dict(self._runtime_environment or {}),
            supervision_proof=self._supervision_proof(),
            executor_instance_id=_EXECUTOR_INSTANCE_ID,
        )

    def _supervision_proof(self) -> dict:
        """返回执行器直接观测，不把自我声明冒充 OS 安全证明。"""
        proc = self._proc
        return {
            "worker_pid": getattr(proc, "pid", None),
            "worker_alive_observed": bool(proc is not None and proc.is_alive()),
            "worker_ready_acknowledged": bool(self._ready_ok),
            "windows_job_object_attached": self._job is not None,
            "parent_watchdog_configured": True,
        }

    def _recv(self, overall: float) -> dict:
        """等一条 response；worker 死亡立即抛 _WorkerDead（fail-fast，P16b）。

        为什么必须轮询 is_alive：multiprocessing Queue 父进程也持有写端，
        worker 被强杀（C 崩溃）时另一端关闭不会触发 EOF → 裸 get 会阻塞到
        超时。轮询进程存活可让"运行中崩溃"快速返回，而非干等 300s。
        """
        deadline = time.time() + overall
        while True:
            with self._lifecycle_lock:
                proc = self._proc
                response_q = self._response_q
            if proc is None or response_q is None or not proc.is_alive():
                raise _WorkerDead()
            remaining = deadline - time.time()
            if remaining <= 0:
                raise queue.Empty
            try:
                return response_q.get(timeout=min(0.5, remaining))
            except queue.Empty:
                continue
            except (EOFError, OSError, ValueError):
                raise _WorkerDead()

    def _await_ready(self) -> bool:
        """启动握手（P16 #3）：等 worker 发 ready；短超时 _START_TIMEOUT。

        worker 引擎 init 失败/卡死时不发 ready → 这里超时快速判启动失败，
        不再干等 _DEFAULT_EXEC_TIMEOUT。失败即清理，返回 False。
        """
        if self._ready_ok:
            return True
        try:
            msg = self._recv(_START_TIMEOUT)
        except (_WorkerDead, queue.Empty):
            self._cleanup()
            return False
        if msg and msg.get("type") == "ready":
            self._ready_ok = True
            self._runtime_environment = dict(msg.get("runtime_environment") or {})
            return True
        self._cleanup()
        return False

    def _log(self, seq: int, code: str, rc: int) -> None:
        """追加一条命令日志（环形，上限 _journal_max）。"""
        self._journal.append({"seq": seq, "cmd": code, "rc": rc})
        if len(self._journal) > self._journal_max:
            self._journal.pop(0)

    def _journal_snapshot(self) -> list[dict]:
        """日志副本（**调用方须已持有 self._lock**）。execute/_make_reset 在锁内用，
        避免死锁（P16b：journal() 公共方法加锁，不能再在锁内调它）。"""
        return [dict(e) for e in self._journal]

    def journal(self) -> list[dict]:
        """当前会话命令日志副本（seq/cmd/rc）。worker 崩溃、会话重置后仍保留。"""
        with self._lock:
            return self._journal_snapshot()

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
            if not self._await_ready():
                return {"reset": True, "start_failed": True}
            self._last_used = time.time()
            self._msg_id += 1
            mid = self._msg_id
            self._request_q.put({"id": mid, "type": "rows", "n": int(n)})
            try:
                resp = self._recv(_READ_TIMEOUT)
            except (_WorkerDead, queue.Empty):
                self._cleanup()
                return {"reset": True}
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
            if not self._await_ready():
                return {"reset": True, "start_failed": True}
            self._last_used = time.time()
            self._msg_id += 1
            mid = self._msg_id
            self._request_q.put({"id": mid, "type": "snapshot"})
            try:
                resp = self._recv(_READ_TIMEOUT)
            except (_WorkerDead, queue.Empty):
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

    def session_status(self, session_id: str) -> dict:
        """查询已登记 session；绝不因查询而创建或启动 Stata worker。"""
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return {"session_id": session_id, "tracked": False, "state": "absent"}
            return {"tracked": True, **session.status_snapshot()}

    def close_session(self, session_id: str) -> dict:
        """从 manager 摘除并强制关闭一个 session，不影响其他 session。"""
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            return {
                "session_id": session_id,
                "tracked_before": False,
                "closed": False,
                "state": "absent",
            }
        snapshots = session.force_close()
        return {
            "session_id": session_id,
            "tracked_before": True,
            "closed": True,
            **snapshots,
        }

    def close_all(self) -> None:
        with self._lock:
            for s in self._sessions.values():
                s.close()
            self._sessions.clear()


# 进程内单例
_manager: SessionManager | None = None
_manager_lock = threading.Lock()


def get_manager() -> SessionManager:
    """返回单例 SessionManager（P16e #4：懒初始化加锁，防并发建多个 manager
    把会话分裂、绕过 max_sessions）。"""
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                _manager = SessionManager()
    return _manager
