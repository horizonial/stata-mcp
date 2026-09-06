import sys
sys.path.insert(0, "src")
from stata_mcp.session import SessionManager
from stata_mcp.tools.run import stata_run


class Ctx:
    def __init__(self, s):
        self.backend = s


def main():
    mgr = SessionManager()
    ctx = Ctx(mgr.get_or_create("default"))
    stata_run({"code": "sysuse auto, clear"}, ctx)

    # 1) 命令报错
    r = stata_run({"code": "regress no_such_var"}, ctx)
    print("=== [1] 命令报错 ===")
    print("  rc:", r.rc, "error_class:", r.error_class)
    print("  error:", r.error)

    # 2) 超时（用 session.execute 直接传短 timeout）
    s = mgr.get_or_create("default")
    r2 = s.execute("sleep 10000", timeout=1.0)
    print("\n=== [2] 超时 ===")
    print("  rc:", r2.rc, "error_kind:", r2.error_kind)
    print("  text:", r2.text[:80])

    mgr.close_all()


if __name__ == "__main__":
    main()
