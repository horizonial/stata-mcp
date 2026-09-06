import sys
sys.path.insert(0, "src")
from stata_mcp.session import SessionManager
from stata_mcp.tools.run import stata_run

class Ctx:
    def __init__(self, s): self.backend = s

def main():
    mgr = SessionManager()
    ctx = Ctx(mgr.get_or_create("default"))

    # 受限模式：shell 应被拦截
    r = stata_run({"code": "shell del C:\important.txt", "restricted": True}, ctx)
    print("[1] restricted shell:", "rc=", r.rc, "|", r.text[:60])

    # 受限模式：正常命令放行
    r = stata_run({"code": "sysuse auto, clear", "restricted": True}, ctx)
    print("[2] restricted sysuse:", "rc=", r.rc)

    # 非受限模式：shell 不拦（默认行为，本地可信）
    r = stata_run({"code": "display 2+2"}, ctx)
    print("[3] normal display:", "rc=", r.rc, "|", r.text.strip())

    mgr.close_all()

if __name__ == "__main__":
    main()
