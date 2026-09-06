import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from stata_mcp.stata.pystata_backend import get_backend
from stata_mcp.results.regression import MATA_COEF_TABLE

b = get_backend()
b.execute("sysuse auto, clear")
b.execute("regress mpg weight price displacement")
r = b.execute(MATA_COEF_TABLE)
print("=== 完整输出 ===")
print(r.text)
