"""P3 探测：Mata 算系数表（统一列向量，修 conformability）。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from stata_mcp.stata.pystata_backend import PystataBackend

MATA = r'''
mata:
    b = st_matrix("e(b)")'
    se = sqrt(diagonal(st_matrix("e(V)")))
    t = b :/ se
    df = .
    if (st_global("e(df_r)") != "") df = strtoreal(st_global("e(df_r)"))
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

def main():
    b = PystataBackend()
    b.init()
    b.execute("sysuse auto, clear")

    from sfi import Matrix, Scalar

    for cmd in ["regress mpg weight price displacement", "logit foreign weight price"]:
        b.execute(cmd)
        b.execute(MATA)
        varnames = Matrix.getColNames("e(b)")
        m = Matrix.get("_coefs")
        print(f"=== {cmd} ===")
        for i, v in enumerate(varnames):
            coef, se, t, p, ll, ul = m[i]
            print(f"  {v:14s} coef={coef:+.6f} se={se:.6f} t={t:.3f} p={p:.4f} ci=[{ll:.6f},{ul:.6f}]")

    print("DONE")

if __name__ == "__main__":
    main()
