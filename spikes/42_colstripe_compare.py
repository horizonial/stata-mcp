import sys
sys.path.insert(0, "src")
from stata_mcp.stata.pystata_backend import get_backend

b = get_backend()
b.execute("sysuse auto, clear")

mata = r'''
mata:
    S = st_matrixcolstripe("e(b)")
    n = rows(S)
    out = J(1, n, "")
    for (i=1; i<=n; i++) {
        out[i] = "[" + S[i,1] + "]" + S[i,2]
    }
    st_global("_mcp_dbg", invtokens(out, "|"))
end
'''

for cmd in ["regress mpg weight price", "logit foreign weight price", "mlogit rep78 weight"]:
    b.execute(cmd)
    r = b.execute(mata)
    from sfi import Macro, Matrix
    print(f"=== {cmd} ===")
    print("  rc:", r.rc, "colstripe:", repr(Macro.getGlobal("_mcp_dbg"))[:200])
    print("  getColNames:", Matrix.getColNames("e(b)"))
