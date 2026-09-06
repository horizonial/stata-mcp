"""P11 spike：验证通用机制的边界——mixed/sem 等"非标准"命令的 e(b) 形状。"""
import sys
sys.path.insert(0, "src")
from stata_mcp.stata.pystata_backend import get_backend
from stata_mcp.tools.run import stata_run

b = get_backend()
b.execute("sysuse auto, clear")

CMDS = [
    "regress mpg weight price",            # 标准：1xk
    "mixed mpg weight || rep78:",          # 混合模型：含方差分量
    "sem (mpg <- weight price)",           # 结构方程
    "xtreg mpg weight, fe",               # 面板（但 auto 无面板 id，会报错）
]

for cmd in CMDS:
    r = stata_run({"code": cmd}, None)
    if r.rc != 0:
        print(f"[FAIL rc={r.rc}] {cmd[:40]}  {r.text.strip()[:60]}")
    elif r.structured is None:
        print(f"[NO-STRUCT] {cmd[:40]}")
    else:
        coefs = r.structured.get("coefs", [])
        print(f"[OK {len(coefs)}coef] {cmd[:38]} vars={[c['var'] for c in coefs][:6]}")
        # 检查是否"悄悄给错"——打印系数值看是否合理
        if coefs:
            print(f"    首系数: {coefs[0]}")

# 单独看 mixed 的 e(b) 形状
b.execute("mixed mpg weight || rep78:")
from sfi import Matrix, Macro
print("\n=== mixed e(b) 形状与列名 ===")
try:
    eb = Matrix.get("e(b)")
    print("e(b) rows:", len(eb), "cols:", len(eb[0]) if eb else 0)
    print("colnames:", Matrix.getColNames("e(b)"))
except Exception as e:
    print("读 e(b) 失败:", e)
