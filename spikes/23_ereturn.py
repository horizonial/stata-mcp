import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from stata_mcp.stata.pystata_backend import get_backend
from sfi import Scalar

b = get_backend()
b.execute("sysuse auto, clear")
b.execute("logit foreign weight price")

print("=== logit 的 e() 标量探查 ===")
for name in ["e(N)", "e(df_m)", "e(df_r)", "e(chi2)", "e(ll)", "e(r2_p)", "e(rank)"]:
    try:
        v = Scalar.getValue(name)
        print(f"  {name} = {v!r}")
    except Exception as e:
        print(f"  {name} = <不存在: {type(e).__name__}>")

# 关键：e(df_r) 对 logit 存在吗？值是什么？
print("\n=== 用 Mata st_numscalar 读 e(df_r) ===")
r = b.execute('mata: st_matrix("_tmp", st_numscalar("e(df_r)"))')
print("rc =", r.rc)
from sfi import Matrix
try:
    print("_tmp =", Matrix.get("_tmp"))
except Exception as e:
    print("_tmp ERR:", e)
