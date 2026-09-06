import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from stata_mcp.stata.pystata_backend import get_backend
from stata_mcp.results.regression import MATA_COEF_TABLE

b = get_backend()
b.execute("sysuse auto, clear")
b.execute("regress mpg weight price displacement")

# 用 execute（capture noisily 包裹）跑 MATA，看能否创建 _coefs
r = b.execute(MATA_COEF_TABLE)
print("execute(MATA) rc =", r.rc)
from sfi import Matrix
try:
    print("_coefs =", Matrix.get("_coefs"))
except Exception as e:
    print("_coefs ERR:", e)
