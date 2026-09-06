import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from stata_mcp.stata.pystata_backend import get_backend

b = get_backend()
b.execute("sysuse auto, clear")

MATA = r'''
mata:
    b = st_matrix("e(b)")'
    se = sqrt(diagonal(st_matrix("e(V)")))
    t = b :/ se
    df = st_numscalar("e(df_r)")
    "df =", df
    if (missing(df)) {
        p = 2*normal(-abs(t))
        crit = invnormal(0.975)
    }
    else {
        p = 2*ttail(df, abs(t))
        crit = invttail(df, 0.025)
    }
    st_matrix("_coefs", (b, se, t, p, b - crit*se, b + crit*se))
end
'''

for cmd in ["regress mpg weight", "logit foreign weight"]:
    b.execute(cmd)
    r = b.execute(MATA)
    from sfi import Matrix
    print(f"=== {cmd} ===")
    print("rc =", r.rc)
    try:
        print("_coefs =", Matrix.get("_coefs"))
    except Exception as e:
        print("ERR:", e)
