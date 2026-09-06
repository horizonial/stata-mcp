"""P15 实测：命令日志记录 + 崩溃后 replay 返回。"""
import sys, time
sys.path.insert(0, "src")
from stata_mcp.session import SessionManager


def main():
    mgr = SessionManager(max_sessions=1)
    try:
        s = mgr.get_or_create("default")

        # 1) 跑几条命令 → journal 记录
        s.execute("sysuse auto, clear")
        s.execute("gen w2 = weight^2")
        r = s.execute("regress mpg weight w2")
        print("[1] journal 条数:", len(s.journal()))
        for e in s.journal():
            print(f"    seq={e['seq']} rc={e['rc']} cmd={e['cmd'][:35]!r}")

        # 2) 崩溃：杀 worker → execute 触发重建 → replay 应带崩溃前历史
        pid = s._proc.pid
        s._proc.terminate(); s._proc.join(timeout=3); time.sleep(0.3)
        r2 = s.execute("display 2+2")
        print("[2] 崩溃后 execute:", repr(r2.text.strip()), "reset:", r2.reset)
        if r2.replay:
            print("    replay 条数:", len(r2.replay))
            print("    replay 前2条 cmd:", [e['cmd'][:30] for e in r2.replay[:2]])
        else:
            print("    replay: None (期望有崩溃前历史)")
    finally:
        mgr.close_all()


if __name__ == "__main__":
    main()
