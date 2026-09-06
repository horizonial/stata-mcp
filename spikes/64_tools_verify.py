import sys
sys.path.insert(0, "src")
from stata_mcp.session import SessionManager
from stata_mcp.tools.run import stata_run
from stata_mcp.tools.get_help import stata_get_help
from stata_mcp.tools.data_rows import stata_data_rows


class Ctx:
    def __init__(self, s):
        self.backend = s


def main():
    mgr = SessionManager()
    ctx = Ctx(mgr.get_or_create("default"))

    # help: regress 前几行
    r = stata_get_help({"topic": "regress"}, ctx)
    print("[1] help regress rc:", r.rc, "| 前80字:", r.text[:80].replace("\n", " "))

    # data_rows
    stata_run({"code": "sysuse auto, clear"}, ctx)
    r = stata_data_rows({"rows": 3}, ctx)
    print("[2] data_rows rc:", r.rc)
    st = r.structured or {}
    print("    variables:", st.get("variables", [])[:4])
    print("    首行:", st.get("rows", [[]])[0] if st.get("rows") else "空")

    mgr.close_all()


if __name__ == "__main__":
    main()
