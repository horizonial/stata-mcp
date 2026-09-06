import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from stata_mcp.stata.pystata_backend import get_backend

b = get_backend()
b.execute("sysuse auto, clear")
b.execute("logit foreign weight price")

# 用一条单行 mata 打印 df 值（避免多行块问题）
r = b.execute('mata: st_local("dfval", strofreal(st_numscalar("e(df_r)")))\nmata: st_local("ismiss", strofreal(missing(st_numscalar("e(df_r)"))))')
print("rc:", r.rc)
from sfi import Macro
print("e(df_r) 值(字符串化) =", repr(Macro.getLocal("dfval")))
print("missing(df) =", repr(Macro.getLocal("ismiss")))

# 也看 e() 里到底有哪些标量
b.execute("ereturn list")
print("--- ereturn list ---")
print(b.execute("display 1").text[:50])
