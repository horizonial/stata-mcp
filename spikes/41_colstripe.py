import sys
sys.path.insert(0, "src")
from stata_mcp.stata.pystata_backend import get_backend

b = get_backend()
b.execute("sysuse auto, clear")
b.execute("mlogit rep78 weight price")

mata = r'''
mata:
    S = st_matrixcolstripe("e(b)")
    n = rows(S)
    parts = J(n, 1, "")
    for (i=1; i<=n; i++) {
        eq = S[i,1]
        nm = S[i,2]
        parts[i] = (eq == "" ? nm : eq + ":" + nm)
    }
    st_global("_mcp_names", invtokens(parts', "|"))
end
'''
r = b.execute(mata)
print("mata rc:", r.rc)
from sfi import Macro
print("colnames:", Macro.getGlobal("_mcp_names"))
