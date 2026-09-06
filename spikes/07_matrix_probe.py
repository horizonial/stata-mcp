"""P3 探测：sfi.Matrix 怎么读 e(b)/e(V)，以及矩阵行/列名。

用 .venv 跑： .venv/Scripts/python.exe spikes/07_matrix_probe.py
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
    print("[Matrix] 成员:", [m for m in dir(Matrix) if not m.startswith("_")])
    print()

    # 尝试常见方法名
    for mname in ["getMatrix", "get", "getMatrixColNames", "getMatrixRowNames", "getColNames", "getRowNames"]:
        fn = getattr(Matrix, mname, None)
        if fn is not None:
            print(f"  Matrix.{mname} 存在")
    print()

    # 试读 e(b)
    try:
        bm = Matrix.getMatrix("e(b)")
        print("[e(b)] 类型:", type(bm), "值:", bm)
    except Exception as e:
        print("[e(b)] getMatrix ERR:", e)

    DONE = True
    print("DONE")

if __name__ == "__main__":
    main()
