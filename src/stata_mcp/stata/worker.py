"""worker 子进程：一个懒启动的 Stata 会话执行器（P10a 会话隔离）。

主进程（MCP server）通过三个 multiprocessing.Queue 与 worker 通信：
- ``request_q``：execute / close（顺序处理）；
- ``response_q``：execute 的结果（text/rc/e_changed/structured）；
- ``break_q``：break 信号，走**独立线程**监听——因为 execute 可能阻塞在长命令，
  若 break 也走 request_q 会排在被卡住的 execute 后面，永远等不到。

为什么结构化解析在 worker 内做：sfi 读的是进程内 Stata 内存（e(b)/e(V)/e()），
主进程读不到 worker 的内存，所以"执行 + 结构化"都必须在 worker 进程内完成，
主进程只拿回解析好的 dict。

会话孤儿看门狗（P14）：若主进程被强杀（SIGKILL/崩溃/taskkill /F），multiprocessing
的 daemon 清理不执行，worker 会变孤儿、继续占着 Stata license。worker 内置一个
父进程存活检测线程：父进程死则主动 ``os._exit(0)``，让 Stata engine 随进程退出
释放 license。Windows 用 ctypes OpenProcess 检测（os.kill 在 Windows 语义不同）。

Windows 下 multiprocessing 用 spawn，worker_main 必须是模块顶层函数。
"""
from __future__ import annotations

import ctypes
import hashlib
import os
import sys
import threading
import time

_RUNTIME_PROBE_ATTEMPTS = 3
_RUNTIME_PROBE_DELAY_SECONDS = 0.05


class RuntimeEnvironmentProbeError(RuntimeError):
    """Raised when the worker cannot prove its required Stata environment."""


def _probe_runtime_value(
    backend,
    *,
    key: str,
    expression: str,
    attempts: int = _RUNTIME_PROBE_ATTEMPTS,
) -> str:
    """Read one required Stata runtime fact with a small bounded retry.

    PyStata occasionally returns an empty capture for the first command after
    engine initialization.  An empty value is not evidence of a supported
    runtime, so the worker retries locally and fails closed before announcing
    ``ready`` if the fact still cannot be observed.
    """
    if attempts < 1:
        raise ValueError("attempts must be positive")
    last_observation = "no observation"
    for attempt in range(1, attempts + 1):
        try:
            result = backend.execute(f"display {expression}")
            lines = [line.strip() for line in result.text.splitlines() if line.strip()]
            if result.rc == 0 and lines:
                return lines[-1]
            last_observation = f"rc={result.rc}, output={'present' if lines else 'empty'}"
        except Exception as exc:  # noqa: BLE001 - backend/engine faults are retryable here
            last_observation = f"exception={type(exc).__name__}"
        if attempt < attempts:
            time.sleep(_RUNTIME_PROBE_DELAY_SECONDS)
    raise RuntimeEnvironmentProbeError(
        f"required runtime fact {key!r} unavailable after {attempts} attempts "
        f"({last_observation})"
    )


def _runtime_environment(backend) -> dict:
    """Collect the required, non-credential runtime identity before ready."""
    import platform

    from .. import __version__

    out = {
        "stata_mcp_version": __version__,
        "backend": "pystata",
        "python_version": platform.python_version(),
        "os": platform.system(),
        "machine": platform.machine(),
    }
    for key, expression in (
        ("stata_version", "c(stata_version)"),
        ("stata_flavor", "c(flavor)"),
        ("stata_os", "c(os)"),
        ("stata_mp", "c(MP)"),
    ):
        out[key] = _probe_runtime_value(
            backend,
            key=key,
            expression=expression,
        )
    out["stata_is_mp"] = out["stata_mp"] == "1"
    return out


def _parent_alive(pid: int) -> bool:
    """Windows 上检测 pid 进程是否存活（OpenProcess + CloseHandle）。

    OpenProcess 对不存在的进程返回 0（失败）；存在返回句柄。这是跨"父进程
    是否还活着"的可靠判据（os.kill 在 Windows 只支持少数信号）。
    """
    try:
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h:
            return False
        ctypes.windll.kernel32.CloseHandle(h)
        return True
    except Exception:
        # 非 Windows / 调用失败：保守认为父进程活着（宁可不误杀，让其它机制兜底）
        return True


def _parent_watchdog(parent_pid: int, interval: float = 2.0) -> None:
    """周期检测父进程；父进程死了就退出本 worker（释放 Stata license）。"""
    while True:
        time.sleep(interval)
        if not _parent_alive(parent_pid):
            if os.environ.get("STATAMCP_DEBUG"):
                print(f"[watchdog] parent {parent_pid} dead; exiting", file=sys.__stderr__)
            os._exit(0)  # 直接退出，不经 Python 清理（进程退出即释放 engine/license）


def _data_signature(backend) -> str | None:
    """读当前数据集的 datasignature，同时保持用户命令留下的 r()。

    ``datasignature`` 本身会覆盖 r()。若 MCP 直接执行它，用户随后引用
    ``r(mean)`` 等值时会得到错误状态，破坏 Stata 会话保真。这里使用 Stata
    自带的 ``_return hold/restore`` 在一个局部执行块内保存并恢复完整 r()。
    """
    code = (
        "tempname __stata_mcp_rhold\n"
        "_return hold `__stata_mcp_rhold'\n"
        "datasignature\n"
        "_return restore `__stata_mcp_rhold'"
    )
    # PyStata occasionally returns an empty internal-command capture even though the user's
    # preceding command succeeded.  A bounded retry is safe because every attempt holds and
    # restores r(), and it prevents a successful data load from losing its provenance token.
    for _attempt in range(3):
        try:
            r = backend.execute(code)
            if r.rc != 0:
                continue
            lines = [line.strip() for line in r.text.splitlines() if line.strip()]
            # _return restore 在部分 Stata/PyStata 组合会额外打印一个 ``.``；
            # datasignature 本体含 ``:``，优先选择它而不是盲取最后一行。
            for line in reversed(lines):
                candidate = line.strip().strip('"')
                if candidate != "." and ":" in candidate:
                    return candidate
        except Exception:
            continue
    return None


def parse_execution_result(backend, execution_result, parse_fn=None) -> tuple[dict | None, str]:
    """Worker 的唯一解析状态转换；允许测试在 parser 边界注入故障。"""
    if execution_result.rc != 0 or not execution_result.e_changed:
        return None, "not_applicable"
    if parse_fn is None:
        from ..results.parser import try_parse

        parse_fn = try_parse
    try:
        structured = parse_fn(backend)
    except Exception:
        return None, "parse_failed"
    if structured is None:
        return None, "parse_failed"
    return structured, "complete"


def capture_return_state(backend, execution_result) -> dict:
    """在任何内部命令覆盖 r() 之前捕获本次命令的 return state。

    ``_data_signature`` 会执行 ``datasignature`` 并改写 r()。因此 r-class 结果
    不能等到主进程随后发 snapshot 才读取，必须在 worker 的同一次 execute
    响应内捕获。这里只返回结构化 r()，不把内部 ``return list`` 计作用户命令。
    """
    if execution_result.rc != 0:
        return {}
    try:
        from ..results.parser import _parse_return_list

        listed = backend.execute("return list")
        return _parse_return_list(listed.text)
    except Exception:
        return {}


# 载入数据类命令：出现即视为"换了一个数据集源"（P0-4 do-file 往返的载入前缀）。
_LOADERS = {
    "sysuse", "webuse", "use", "import", "insheet", "infile",
    "append", "merge", "copy",
}
# 清空数据命令：出现即视为"数据源清空"（之前记录的载入前缀失效）。
_CLEARERS = {"clear"}


def _is_loader(code: str) -> bool:
    first = code.strip().split(None, 1)[0].lower().rstrip(",")
    return first in _LOADERS


def _is_clearer(code: str) -> bool:
    first = code.strip().split(None, 1)[0].lower().rstrip(",")
    return first in _CLEARERS


def _configure_worker_temp_directory(worker_temp_directory: str | None) -> None:
    """Bind Stata and Python temporary files to this worker's private scope."""
    if worker_temp_directory is None:
        return
    path = os.path.abspath(worker_temp_directory)
    os.makedirs(path, exist_ok=True)
    # STATATMP is Stata's documented override and takes precedence over the
    # ordinary operating-system temporary-directory variables.
    os.environ["STATATMP"] = path
    os.environ["TEMP"] = path
    os.environ["TMP"] = path


def worker_main(
    request_q,
    response_q,
    break_q,
    parent_pid: int | None = None,
    worker_temp_directory: str | None = None,
) -> None:
    """worker 入口：init pystata，循环处理命令。

    除执行外，跟踪会话级溯源状态（P0-4）：
    - ``_data_load_cmd``：最近一次"载入数据"的命令（do-file 往返的载入前缀）；
    - ``_exec_seq``：单调执行序号（结果版本化）。

    ``parent_pid``：主进程 PID，用于孤儿看门狗（P14）——主进程死则本 worker 自退。
    """
    _configure_worker_temp_directory(worker_temp_directory)

    from .pystata_backend import PystataBackend
    backend = PystataBackend()
    backend.init()
    runtime_environment = _runtime_environment(backend)
    _data_load_cmd: str | None = None
    _exec_seq = 0

    # 启动握手（P16 #3）：引擎 init 完成即发 ready。init 抛异常 → 本进程退出、
    # 永不发 ready → Session 端短超时判"启动失败"，不再干等 300s。
    response_q.put(
        {"id": 0, "type": "ready", "runtime_environment": runtime_environment}
    )

    if parent_pid:
        if os.environ.get("STATAMCP_DEBUG"):
            print(f"[worker] starting watchdog for parent {parent_pid}", file=sys.__stderr__)
        threading.Thread(
            target=_parent_watchdog, args=(parent_pid,), daemon=True
        ).start()

    # 独立 break 线程：阻塞等 break 信号，收到即 interrupt（不持 backend 锁，
    # 见 PystataBackend.interrupt 的说明），能打断正在阻塞的长命令。
    def _break_listener() -> None:
        while True:
            break_q.get()
            try:
                backend.interrupt()
            except Exception:
                pass

    threading.Thread(target=_break_listener, daemon=True).start()

    while True:
        msg = request_q.get()
        typ = msg["type"]
        if typ == "execute":
            code = msg["code"]
            r = backend.execute(code)
            _exec_seq += 1
            return_state = capture_return_state(backend, r)
            structured, structured_result_status = parse_execution_result(backend, r)
            # 追踪数据源（do-file 往返）：载入命令记下，clear 清掉。
            if r.rc == 0:
                if _is_clearer(code):
                    _data_load_cmd = None
                elif _is_loader(code):
                    _data_load_cmd = code
            response_q.put(
                {
                    "id": msg["id"],
                    "text": r.text,
                    "rc": r.rc,
                    "e_changed": r.e_changed,
                    "structured": structured,
                    "structured_result_status": structured_result_status,
                    "return_state": return_state,
                    # 溯源元数据：命令哈希 + 数据指纹 + 数据源命令 + 执行序号
                    "command_hash": hashlib.sha256(code.encode("utf-8")).hexdigest()[:16],
                    "data_signature": _data_signature(backend),
                    "data_load_cmd": _data_load_cmd,
                    "exec_seq": _exec_seq,
                }
            )
        elif typ == "snapshot":
            # 只读当前 e()/r()/shape/变量 状态（get_results/inspect/load 用）。
            from ..results.parser import snapshot_state

            try:
                snap = snapshot_state(backend)
            except Exception:
                snap = {}
            response_q.put({"id": msg["id"], **snap})
        elif typ == "rows":
            # 数据预览：读前 n 行（P1b data_rows）。worker 内 sfi.Data 读取。
            from ..results.parser import read_rows

            try:
                rows = read_rows(backend, msg.get("n", 10))
            except Exception:
                rows = {}
            response_q.put({"id": msg["id"], **rows})
        elif typ == "close":
            break
