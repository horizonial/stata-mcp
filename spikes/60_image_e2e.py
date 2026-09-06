import sys
sys.path.insert(0, "src")
from stata_mcp.session import SessionManager
from stata_mcp.tools.run import stata_run
from stata_mcp.tools.export_graph import stata_export_graph


class Ctx:
    def __init__(self, s):
        self.backend = s


def main():
    mgr = SessionManager()
    ctx = Ctx(mgr.get_or_create("default"))
    stata_run({"code": "sysuse auto, clear\ntwoway (scatter mpg weight), name(g1)"}, ctx)
    r = stata_export_graph({"format": "png", "name": "g1"}, ctx)
    print("export rc:", r.rc)
    print("structured:", r.structured)
    print("images count:", len(r.images))
    if r.images:
        mime, data = r.images[0]
        print("mime:", mime, "bytes:", len(data))
        # PNG 魔数校验
        print("is PNG:", data[:8] == b"\x89PNG\r\n\x1a\n")
    mgr.close_all()


if __name__ == "__main__":
    main()
