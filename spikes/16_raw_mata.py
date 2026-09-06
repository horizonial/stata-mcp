"""P3 调试：execute_raw 跑 Mata 到底报什么。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from stata_mcp.stata.pystata_backend import get_backend
from stata_mcp.results.regression import MATA_COEF_TABLE

def main():
    b = get_backend()
    b.execute("sysuse auto, clear")
    b.execute("regress mpg weight price displacement")

    print("=== MATA_COEF_TABLE 原文 ===")
    print(repr(MATA_COEF_TABLE))

    r = b.execute_raw(MATA_COEF_TABLE)
    print("\n=== execute_raw 返回 rc =", r.rc, "===")
    print("text:", repr(r.text))

    from sfi import Matrix
    try:
        print("_coefs:", Matrix.get("_coefs"))
    except Exception as e:
        print("_coefs ERR:", e)

if __name__ == "__main__":
    main()
