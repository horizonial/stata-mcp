import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from stata_mcp.stata.pystata_backend import get_backend

b = get_backend()
b.execute("sysuse auto, clear")
b.execute("logit foreign weight price")

from sfi import Scalar, Matrix
print("=== logit e() 标量 ===")
for name in ["e(N)", "e(df_m)", "e(df_r)", "e(chi2)", "e(r2_p)", "e(rank)"]:
    try:
        print(f"  {name} = {Scalar.getValue(name)!r}")
    except Exception as e:
        print(f"  {name} = <{type(e).__name__}>")

print("\n=== st_numscalar 读 e(df_r) ===")
r = b.execute('mata: st_matrix("_tmp", st_numscalar("e(df_r)"))')
print("rc =", r.rc)
try:
    print("_tmp =", Matrix.get("_tmp"))
except Exception as e:
    print("_tmp ERR:", type(e).__name__, e)
