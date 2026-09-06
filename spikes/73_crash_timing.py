"""P16b 实测：worker 运行中被强杀后，下一次 execute 是否 fail-fast（而非等 300s）。"""
import os
import subprocess
import sys
import time

sys.path.insert(0, "src")
from stata_mcp.session import SessionManager


def main():
    mgr = SessionManager(max_sessions=1)
    try:
        s = mgr.get_or_create("default")
        r = s.execute("display 1")  # 点火
        print("[0] 点火 ok rc:", r.rc)
        pid = s._proc.pid

        # 模拟运行中崩溃：外部强杀 worker（taskkill /F，模拟 C 引擎段错误）
        subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                       capture_output=True, text=True)
        time.sleep(0.5)
        print("[1] worker 已强杀 pid", pid)

        # 下一次 execute：等多久？
        t0 = time.time()
        r2 = s.execute("display 2+2")
        dt = time.time() - t0
        print(f"[2] 崩溃后 execute 返回: rc={r2.rc} reset={r2.reset} 耗时 {dt:.2f}s")
        print("    (若 <2s = fail-fast 靠 EOF；若 ~300s = 干等，需修)")
        print("    text:", r2.text[:80])
    finally:
        mgr.close_all()


if __name__ == "__main__":
    main()
