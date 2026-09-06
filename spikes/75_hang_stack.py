import faulthandler
import subprocess
import sys
import time

sys.path.insert(0, "src")
from stata_mcp.session import SessionManager

faulthandler.dump_traceback_later(15, exit=True)  # 15s 后 dump 栈


def main():
    mgr = SessionManager(max_sessions=1)
    try:
        s = mgr.get_or_create("default")
        r = s.execute("display 1")
        print("ignition ok", r.rc, flush=True)
        pid = s._proc.pid
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, text=True)
        time.sleep(0.5)
        print("killed, is_alive:", s.is_alive(), flush=True)
        t0 = time.time()
        r2 = s.execute("display 2+2")
        print("returned", r2.rc, f"{time.time()-t0:.1f}s", flush=True)
    finally:
        mgr.close_all()


if __name__ == "__main__":
    main()
