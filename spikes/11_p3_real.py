"""P3 实测：验证结构化结果 + 存疑点（e()残留过期、logistic OR）。

用 .venv 跑： .venv/Scripts/python.exe spikes/11_p3_real.py
"""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from stata_mcp.stata.pystata_backend import get_backend
from stata_mcp.tools.run import stata_run

def call(code):
    r = stata_run({"code": code}, None)  # 生产路径：ctx=None，走全局单例
    return r

def main():
    b = get_backend()
    b.execute("sysuse auto, clear")

    # 1) regress -> 应产结构化
    r1 = call("regress mpg weight price displacement")
    print("=== [1] regress 结构化 ===")
    print("rc:", r1.rc, "error_class:", r1.error_class)
    if r1.structured:
        print(json.dumps(r1.structured, ensure_ascii=False, indent=1)[:800])
    else:
        print("structured = None  <-- 问题!")

    # 2) 存疑点1：跑完 regress 再跑 display，e() 残留是否导致过期 structured
    r2 = call("display 2+2")
    print("\n=== [2] display 2+2（存疑点1：e()残留） ===")
    print("rc:", r2.rc, "text:", repr(r2.text[:40]))
    print("structured:", json.dumps(r2.structured, ensure_ascii=False) if r2.structured else None)

    # 3) 出错命令 -> error_class
    r3 = call("regress no_such_var")
    print("\n=== [3] regress 无变量 ===")
    print("rc:", r3.rc, "error_class:", r3.error_class)

    # 4) 存疑点2：logistic 的 e(b) 是 OR 还是 log-odds
    b.execute("logistic foreign weight price")
    from sfi import Matrix
    print("\n=== [4] logistic e(b)（存疑点2） ===")
    print("e(b) =", Matrix.get("e(b)"))
    print("e(cmd) =", Matrix.getColNames("e(b)"))
    # 对照：logit 的 e(b)
    b.execute("logit foreign weight price")
    print("logit e(b) =", Matrix.get("e(b)"))

    print("\nDONE")

if __name__ == "__main__":
    main()
