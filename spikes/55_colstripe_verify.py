"""P11 验证：colstripe 规则改进后，各命令的列名是否都正确区分。"""
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

    for cmd in [
        "regress mpg weight price",
        "logit foreign weight price",
        "mlogit rep78 weight price",
        "oprobit rep78 weight price",
        "mixed mpg weight || rep78:",
    ]:
        r = stata_run({"code": cmd}, ctx)
        if r.rc != 0:
            print(f"[FAIL rc={r.rc}] {cmd[:40]}")
        elif r.structured is None:
            print(f"[NO-STRUCT] {cmd[:40]}")
        else:
            vars_ = [c["var"] for c in r.structured.get("coefs", [])]
            print(f"[OK] {cmd[:38]} -> {vars_}")

    mgr.close_all()


if __name__ == "__main__":
    main()
