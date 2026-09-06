import sys
sys.path.insert(0, "src")
from stata_mcp.stata.pystata_backend import get_backend

b = get_backend()
b.execute("sysuse auto, clear")

# 测一批"没注册"的命令，看 e(b) 是否可读、结构是否通用系数表
CMDS = [
    "regress mpg i.rep78",          # 因子变量列名
    "regress mpg c.weight##c.price", # 交互项列名
    "anova mpg rep78 foreign",       # 方差分析（未注册）
    "regress mpg weight [pw=price]", # 加权
    "sureg (mpg weight) (price displacement)",  # 联立方程（未注册）
    "regress mpg weight, vce(bootstrap, reps(5))",  # bootstrap 标准误
]

mata = r'''
mata:
    b = st_matrix("e(b)")
    ncols = cols(b)
    nrows = rows(b)
    st_numscalar("_dbg_rows", nrows)
    st_numscalar("_dbg_cols", ncols)
    S = st_matrixcolstripe("e(b)")
    parts = J(1, cols(S), "")
    for (i=1; i<=cols(S); i++) {
        eq = S[i,1]
        nm = S[i,2]
        parts[i] = (regexm(eq, "^[0-9]+$") ? eq + ":" + nm : nm)
    }
    st_global("_dbg_names", invtokens(parts, "|"))
end
'''

for cmd in CMDS:
    r = b.execute(cmd)
    if r.rc != 0:
        print(f"[FAIL rc={r.rc}] {cmd[:45]}")
        continue
    from sfi import Macro, Scalar
    # 读 e(b) 形状和列名
    try:
        r2 = b.execute(mata)
        rows = Scalar.getValue("_dbg_rows")
        cols = Scalar.getValue("_dbg_cols")
        names = Macro.getGlobal("_dbg_names")
        print(f"[e(b) {rows}x{cols}] {cmd[:40]}")
        print(f"    列名: {names[:110]}")
    except Exception as e:
        print(f"[e(b) 读失败] {cmd[:40]} {e}")
