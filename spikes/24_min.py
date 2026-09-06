import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from stata_mcp.stata.pystata_backend import get_backend

b = get_backend()
b.execute("sysuse auto, clear")
b.execute("logit foreign weight price")

# 把 st_numscalar 结果写进一个标量，再用 Python 读
r = b.execute('mata: st_numscalar("_tmp_s", st_numscalar("e(df_r)"))')
print("rc:", r.rc)
from sfi import Scalar
try:
    print("_tmp_s =", Scalar.getValue("_tmp_s"))
except Exception as e:
    print("_tmp_s ERR:", type(e).__name__, e)

# 也测 regress 对照
b.execute("regress mpg weight")
b.execute('mata: st_numscalar("_tmp_s2", st_numscalar("e(df_r)"))')
try:
    print("regress _tmp_s2 =", Scalar.getValue("_tmp_s2"))
except Exception as e:
    print("_tmp_s2 ERR:", type(e).__name__, e)
