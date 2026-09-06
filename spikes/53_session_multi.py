"""P10 验证：会话隔离下，多命令结构化结果都正常（走 SessionManager）。

注意：Windows multiprocessing spawn 要求入口脚本有 if __name__ == "__main__"
保护（否则子进程重新 import 本模块时会在顶层递归 spawn）。
"""
import sys
sys.path.insert(0, "src")

from stata_mcp.session import SessionManager
from stata_mcp.tools.run import stata_run
from stata_mcp.tools.get_results import stata_get_results


class Ctx:
    def __init__(self, s):
        self.backend = s


def main():
    mgr = SessionManager()
    session = mgr.get_or_create("default")
    ctx = Ctx(session)

    r = stata_run({"code": "sysuse auto, clear"}, ctx)
    print("[1] sysuse:", r.rc)

    r = stata_run({"code": "regress mpg weight price"}, ctx)
    print("[2] regress:", r.rc, "coefs:", [c['var'] for c in r.structured.get('coefs', [])])
    print("    scalars keys:", sorted(r.structured.get('scalars', {}).keys())[:6])

    r = stata_run({"code": "logit foreign weight"}, ctx)
    print("[3] logit:", r.rc, "coefs:", [c['var'] for c in r.structured.get('coefs', [])])

    stata_run({"code": "scalar _x = 7*6"}, ctx)
    r = stata_run({"code": "display _x"}, ctx)
    print("[4] 持久 _x:", repr(r.text.strip()))

    r = stata_get_results({}, ctx)
    print("[5] get_results structured cmd:", r.structured.get('cmd') if r.structured else None)

    mgr.close_all()
    print("SESSION_MULTI_OK")


if __name__ == "__main__":
    main()
