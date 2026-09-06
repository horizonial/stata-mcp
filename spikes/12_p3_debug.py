"""P3 调试：定位 structured=None 的根因。"""
import sys, os, traceback
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from stata_mcp.stata.pystata_backend import PystataBackend
from stata_mcp.results.parser import try_parse, _get_e_cmd

def main():
    b = PystataBackend()
    b.init()
    b.execute("sysuse auto, clear")
    b.execute("regress mpg weight price displacement")

    print("=== _get_e_cmd() ===")
    print(repr(_get_e_cmd()))

    print("=== try_parse 直接调用 ===")
    try:
        result = try_parse(b)
        print("result:", result)
    except Exception:
        traceback.print_exc()

    print("\n=== 单独跑 Mata ===")
    from stata_mcp.results.regression import MATA_COEF_TABLE
    r = b.execute(MATA_COEF_TABLE)
    print("Mata execute rc:", r.rc)
    print("Mata output:", repr(r.text[:300]))
    from sfi import Matrix
    try:
        print("_coefs:", Matrix.get("_coefs"))
    except Exception as e:
        print("_coefs ERR:", e)

if __name__ == "__main__":
    main()
