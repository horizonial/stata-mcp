import sys, traceback
sys.path.insert(0, "src")
from stata_mcp.stata.pystata_backend import get_backend
from stata_mcp.results.parser import try_parse

b = get_backend()
b.execute("sysuse auto, clear")
b.execute("regress mpg weight price displacement")

try:
    r = try_parse(b)
    print("result:", r)
except Exception:
    traceback.print_exc()

# 看 _mcp_colnames 是什么
from sfi import Macro
print("_mcp_colnames:", repr(Macro.getGlobal("_mcp_colnames")))
