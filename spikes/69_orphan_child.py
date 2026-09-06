"""P14 集成测试的 child：建会话点火，打印 worker pid，os._exit 模拟崩溃。"""
import os
import sys

sys.path.insert(0, os.path.abspath("src"))


def main():
    from stata_mcp.session import SessionManager

    mgr = SessionManager(max_sessions=1)
    s = mgr.get_or_create("default")
    r = s.execute("display 1", timeout=60)
    assert r.rc == 0, f"engine init failed: {r.rc} {r.text[:120]}"
    print(f"WORKER_PID={s._proc.pid}", flush=True)
    print("EXITING_NOW", flush=True)
    os._exit(0)


if __name__ == "__main__":
    main()  # worker 的 mp_main 重入时 __name__ != "__main__"，不会执行
