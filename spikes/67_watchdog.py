"""P14 实测：孤儿看门狗——父进程强杀后 worker 应自退释放 license。

父分支 spawn 子进程（child 模式）：child 建 session + 点火，打印 worker pid，
然后 os._exit(0) 模拟崩溃；父等几秒后检查 worker 是否已自退。
"""
import ctypes
import os
import subprocess
import sys
import time

SRC = os.path.abspath("src")


def _proc_alive(pid: int) -> bool:
    try:
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return False
        ctypes.windll.kernel32.CloseHandle(h)
        return True
    except Exception:
        return False


def _child() -> None:
    sys.path.insert(0, SRC)
    from stata_mcp.session import SessionManager

    mgr = SessionManager(max_sessions=1)
    s = mgr.get_or_create("default")
    r = s.execute("display 1")
    assert r.rc == 0, f"engine init failed: {r.rc} {r.text[:120]}"
    print(f"CHILD_WORKER_PID={s._proc.pid}", flush=True)
    print("CHILD_EXITING_NOW", flush=True)
    os._exit(0)  # 模拟崩溃：绕过 multiprocessing daemon 清理


def _parent() -> None:
    child = subprocess.Popen(
        [sys.executable, __file__, "child"], stdout=subprocess.PIPE, text=True
    )
    worker_pid = None
    for line in child.stdout:
        line = line.strip()
        if line.startswith("CHILD_WORKER_PID="):
            worker_pid = int(line.split("=")[1])
        if line == "CHILD_EXITING_NOW":
            break
    child.wait(timeout=90)
    print(f"[parent] child exit {child.returncode}, worker_pid={worker_pid}")
    if not worker_pid:
        print("FAIL: no worker pid"); sys.exit(1)

    print(f"[parent] 崩溃后立即: worker alive={_proc_alive(worker_pid)}")
    time.sleep(6)  # > 看门狗 2s 间隔 + 余量
    alive = _proc_alive(worker_pid)
    print(f"[parent] 6s 后: worker alive={alive}  (期望 False=已自退释放 license)")
    print("WATCHDOG_" + ("OK" if not alive else "FAIL"))
    sys.exit(0 if not alive else 1)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "child":
        _child()
    else:
        _parent()
