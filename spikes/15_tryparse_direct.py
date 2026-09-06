"""P3 调试：直接调 try_parse 走全局单例，暴露异常。"""
import sys, os, traceback
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from stata_mcp.stata.pystata_backend import get_backend
from stata_mcp.results.parser import try_parse, _get_e_cmd, PARSERS

def main():
    b = get_backend()
    b.execute("sysuse auto, clear")
    b.execute("regress mpg weight price displacement")

    print("e(cmd) =", repr(_get_e_cmd()))
    print("PARSERS keys =", list(PARSERS.keys()))
    print("parser for regress =", PARSERS.get("regress"))

    try:
        r = try_parse(b)
        print("try_parse result =", r)
    except Exception:
        traceback.print_exc()

if __name__ == "__main__":
    main()
