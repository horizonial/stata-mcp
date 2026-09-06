"""P3 探测：sfi 模块能读什么 + 各种命令的 e()/r() 内存结构。

用 .venv 跑： .venv/Scripts/python.exe spikes/06_sfi_probe.py
目的：决定 P3 结构化结果的解析路线（sfi 直读内存 vs 解析文本）。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from stata_mcp.stata.pystata_backend import PystataBackend

def main():
    b = PystataBackend()
    b.init()
    b.execute("sysuse auto, clear")

    # ---- 1) 探 sfi 有哪些类 ----
    import sfi
    print("[sfi] 模块成员:", [m for m in dir(sfi) if not m.startswith("_")])
    print()

    from sfi import Scalar, Macro
    b.execute("regress mpg weight price, robust")
    print("[regress e()] 关键标量:")
    for k in ["e(N)", "e(r2)", "e(r2_a)", "e(F)", "e(df_m)", "e(df_r)", "e(rank)"]:
        try:
            print(f"  {k} = {Scalar.getValue(k)}")
        except Exception as e:
            print(f"  {k} = ERR {e}")

    print("[regress e()] 关键宏:")
    for k in ["e(cmd)", "e(depvar)", "e(vcetype)", "e(properties)"]:
        try:
            print(f"  {k} = {Macro.getGlobal(k)!r}")
        except Exception as e:
            print(f"  {k} = ERR {e}")

    print("DONE")

if __name__ == "__main__":
    main()
