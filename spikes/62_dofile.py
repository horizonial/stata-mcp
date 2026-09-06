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

    # 先载入数据（sysuse → 记 data_load_cmd）
    stata_run({"code": "sysuse auto, clear"}, ctx)
    # 跑回归 → provenance 应含 do_file = "sysuse auto, clear\nregress..."
    r = stata_run({"code": "regress mpg weight price"}, ctx)
    prov = (r.structured or {}).get("provenance", {})
    print("=== provenance ===")
    for k in ("command_hash", "data_signature", "exec_seq", "do_file"):
        print(f"  {k}: {prov.get(k, '')!r}"[:110])

    mgr.close_all()


if __name__ == "__main__":
    main()
