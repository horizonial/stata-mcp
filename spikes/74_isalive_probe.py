"""探测：外部强杀 worker 后，multiprocessing is_alive 是否及时翻 False。"""
import subprocess
import sys
import time

sys.path.insert(0, "src")
from stata_mcp.session import SessionManager


def main():
    mgr = SessionManager(max_sessions=1)
    try:
        s = mgr.get_or_create("default")
        r = s.execute("display 1")
        print("ignition ok", r.rc, flush=True)
        pid = s._proc.pid
        res = subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, text=True)
        print("taskkill rc:", res.returncode, res.stdout.strip(), flush=True)
        for i in range(6):
            time.sleep(1)
            print(f"t+{i+1}s is_alive={s.is_alive()} is_crashed={s.is_crashed()}", flush=True)
        # 直接触发重建（不进 execute 的 300s 等待逻辑，改用短路径验证）
        s._cleanup()
        print("cleaned", flush=True)
    finally:
        mgr.close_all()


if __name__ == "__main__":
    main()
