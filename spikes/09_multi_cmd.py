"""P3 探测：多种命令类型的结构化结果提取。

验证两件事：
  A) Mata 一次性算 coef/se/t/p/ci 是否对 t 分布(regress)和 z 分布(logit)都通用；
  B) 多类命令（估计类 e() 与 描述类 r()）的结果结构。

用 .venv 跑： .venv/Scripts/python.exe spikes/09_multi_cmd.py
"""
import sys, os, math, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from stata_mcp.stata.pystata_backend import PystataBackend

# Mata：从 e(b)/e(V) 算完整系数表，写入 r 类矩阵 _coefs（行=变量，列=coef/se/t/p/ll/ul）
MATA = r'''
mata:
    b = st_matrix("e(b)")
    V = st_matrix("e(V)")
    se = sqrt(diagonal(V))'
    t = b' :/ se
    df = .  // . 表示 z 分布
    if (st_global("e(df_r)") != "") df = strtoreal(st_global("e(df_r)"))
    if (df == .) {
        p = 2*normal(-abs(t))
        crit = invnormal(0.975)
    }
    else {
        p = 2*ttail(df, abs(t))
        crit = invttail(df, 0.025)
    }
    ll = b' :- crit*se
    ul = b' :+ crit*se
    st_matrix("_coefs", (b', se, t, p, ll, ul))
    st_matrixcolstripe("_coefs", st_matrixcolstripe("e(b)"), ("", "se", "t", "p", "ll", "ul"))
end
'''

def read_coef_table(backend, cmd):
    backend.execute(cmd)
    backend.execute(MATA)
    from sfi import Matrix
    names = Matrix.getColNames("_coefs") if False else None
    # 直接读 e(b) 列名 + _coefs 数值
    varnames = Matrix.getColNames("e(b)")
    m = Matrix.get("_coefs")
    rows = []
    for i, v in enumerate(varnames):
        coef, se, t, p, ll, ul = m[i]
        rows.append({"var": v, "coef": round(coef, 6), "se": round(se, 6),
                     "t": round(t, 3), "p": round(p, 4), "ci": [round(ll, 6), round(ul, 6)]})
    return rows

def main():
    b = PystataBackend()
    b.init()
    b.execute("sysuse auto, clear")

    print("=== A1 regress (t 分布) ===")
    print(json.dumps(read_coef_table(b, "regress mpg weight price displacement"), ensure_ascii=False))

    print("=== A2 logit (z 分布) ===")
    b.execute("gen foreign2 = foreign")
    print(json.dumps(read_coef_table(b, "logit foreign weight price"), ensure_ascii=False))

    print("=== B1 summarize (r 类) ===")
    b.execute("summarize mpg, detail")
    from sfi import Scalar
    for k in ["r(N)", "r(mean)", "r(sd)", "r(min)", "r(max)", "r(p50)"]:
        try: print(f"  {k} = {Scalar.getValue(k)}")
        except Exception: pass

    print("=== B2 tabulate (r 类) ===")
    b.execute("tabulate foreign")
    for k in ["r(N)", "r(r)", "r(c)"]:
        try: print(f"  {k} = {Scalar.getValue(k)}")
        except Exception: pass

    print("=== B3 ttest (r 类) ===")
    b.execute("ttest mpg, by(foreign)")
    for k in ["r(N_1)", "r(N_2)", "r(t)", "r(p)", "r(mu_1)", "r(mu_2)"]:
        try: print(f"  {k} = {Scalar.getValue(k)}")
        except Exception: pass

    print("DONE")

if __name__ == "__main__":
    main()
