import sys
sys.path.insert(0, "src")
from stata_mcp.session import SessionManager
from stata_mcp.tools.run import stata_run

class Ctx:
    def __init__(self, s): self.backend = s

def main():
    mgr = SessionManager()
    ctx = Ctx(mgr.get_or_create("default"))
    stata_run({"code": "sysuse auto, clear"}, ctx)
    r = stata_run({"code": "regress mpg weight price"}, ctx)
    st = r.structured or {}
    print("provenance:", st.get("provenance"))
    print("coefs:", [c['var'] for c in st.get('coefs', [])])
    mgr.close_all()

if __name__ == "__main__":
    main()
