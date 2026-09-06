import sys, os, traceback
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from stata_mcp.stata.pystata_backend import get_backend
from stata_mcp.results.regression import MATA_COEF_TABLE
from stata_mcp.results.parser import try_parse, _get_e_cmd

b = get_backend()
b.execute("sysuse auto, clear")
r = b.execute("logit foreign weight price")
print("logit execute rc =", r.rc, "e_changed =", r.e_changed)
print("e(cmd) =", repr(_get_e_cmd()))

print("\n=== 手动跑 MATA ===")
r2 = b.execute(MATA_COEF_TABLE)
print("MATA rc =", r2.rc)
print(r2.text[-400:])
from sfi import Matrix
try:
    print("_coefs =", Matrix.get("_coefs"))
except Exception as e:
    print("_coefs ERR:", e)

print("\n=== try_parse ===")
try:
    print("result:", try_parse(b))
except Exception:
    traceback.print_exc()
