"""P3 探测：正确读 e(b)/e(V) 矩阵 + 列名，验证能否拼出系数表。

用 .venv 跑： .venv/Scripts/python.exe spikes/08_matrix_read.py
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from stata_mcp.stata.pystata_backend import PystataBackend

def main():
    b = PystataBackend()
    b.init()
    b.execute("sysuse auto, clear")
    b.execute("regress mpg weight price displacement")

    from sfi import Matrix

    for matname in ["e(b)", "e(V)"]:
        try:
            m = Matrix.get(matname)
            cols = Matrix.getColNames(matname)
            rows = Matrix.getRowNames(matname)
            print(f"[{matname}]")
            print(f"  get() -> {m!r}")
            print(f"  colNames -> {cols!r}")
            print(f"  rowNames -> {rows!r}")
        except Exception as e:
            print(f"[{matname}] ERR: {e}")
        print()

    # e(V) 的对角线开方 = SE
    try:
        V = Matrix.get("e(V)")
        import math
        se = [math.sqrt(V[i][i]) for i in range(len(V))]
        print("[SE from e(V) 对角线]:", se)
    except Exception as e:
        print("[SE] ERR:", e)

    print("DONE")

if __name__ == "__main__":
    main()
