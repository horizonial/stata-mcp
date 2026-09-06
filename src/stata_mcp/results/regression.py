"""估计类命令的通用系数提取器（DESIGN §8 P3 路线，P8 泛化）。

为什么统计量用 Mata 算而不是 Python 算：
- DESIGN spike06-10 已验证这段 Mata 对 t 分布（regress 等有 e(df_r)）和
  z 分布（logit/probit 等 df 缺失判空）的 p 值 / 95%CI 都正确；在 Stata 侧算，
  Python 侧不重复实现 ttail / invttail 的精度约定。
- ``e(b)`` 是 1×k 行向量、``diagonal(e(V))`` 是 k×1 列向量，混用会
  conformability(3200)；必须先转置 e(b) 成列向量再算（Mata 踩坑，DESIGN 已记录）。

为什么是"通用系数提取"而不是"按命令枚举"（P8 架构重构）：
- 实测（spike45）证明：**所有 e-class 估计命令（regress/logit/xtreg/anova/sureg…）
  的 e(b) 都是 1×k 系数向量、e(V) 是 k×k 协方差矩阵**，这是 Stata 的统一约定。
- 因此系数提取（coef/se/t/p/ci）对任意 e-class 命令通用，**不需要枚举命令名**。
- 本模块提供一个模块级函数 ``parse_coef_table``，由 parser.try_parse 在检测到
  e(b) 存在时直接调用；特殊结构（方差分解、混合模型方差分量等）未来通过
  PARSERS 注册表"按需加深"，覆盖通用路径。

为什么 parse 内部不吞异常：
- 引擎状态异常（e(b) 空、_coefs 读不到）让它冒泡，由 run.py 统一降级
  structured=None；这里不做半吊子容错。
"""
from __future__ import annotations

from .parser import _backend, _get_e_scalar, _num

# 系数表用 Mata 算 coef/se/t/p/95%CI，结果存 Stata 内存矩阵 _coefs。
# 每行 = (b, se, t, p, ll, ul)，与 e(b) 列顺序一一对应。
#
# 踩坑记录（P3 实测，重要）：
# - e(b) 是 1×k 行向量、diagonal(V) 是 k×1 列向量，混用 conformability(3200)，须先转置。
# - t 分布 vs z 分布不能靠 Mata 的 missing(df) 判断：st_numscalar("e(df_r)") 对缺失
#   返回 missing，但 missing() 在 if 里行为不可靠（实测 logit 误走 ttail 报 3200）。
#   因此 df 的判定改在 Python 侧做（_get_e_scalar("df_r") 为 None 即 z 分布），
#   把确定的值注入 Mata，逻辑可预测、可单测。
# - Mata 的 if/else 不能写成单行 `if (c) { a; b }`，会 illegal arglist(r3000)。
# 读 colstripe 拿带方程/参数前缀的完整列名。为什么需要它（P7/P11 实测）：
# mlogit 多方程、mixed 方差分量等模型，getColNames("e(b)") 只返回纯变量名、丢了
# 前缀，导致 weight/_cons 重复、agent 无法区分系数属于哪个方程/哪个参数。
#
# 组合规则（实测对比 regress/logit/mlogit/oprobit/mixed 的 colstripe 得出）：
# - eq 为空（regress/areg 单方程）→ 纯变量名；
# - eq 等于 depvar 名（logit/probit/poisson/mixed 固定部分）→ 纯变量名（多余前缀）；
# - 其它（mlogit 的 "1"/"2" 方程号、mixed 的 "lns1_1_1"/"lnsig_e" 方差分量、
#   oprobit 的 "/" 切点）→ "eq:name"（有区分意义，必须保留）。
_COLSTRIPE_MATA = r'''
    S = st_matrixcolstripe("e(b)")
    depvar = st_global("e(depvar)")
    n = rows(S)
    parts = J(n, 1, "")
    for (i=1; i<=n; i++) {
        eq = S[i,1]
        nm = S[i,2]
        if (eq == "" | eq == depvar) parts[i] = nm
        else parts[i] = eq + ":" + nm
    }
    st_global("_mcp_colnames", invtokens(parts', "|"))
'''

# 注意：MATA 里含 for 循环的花括号，不能用 str.format（花括号会被当占位符），
# 用 .replace 注入 df 值。
_MATA_COEF_TABLE_T = r"""
mata:
    b = st_matrix("e(b)")'
    se = sqrt(diagonal(st_matrix("e(V)")))
    t = b :/ se
    p = 2*ttail(DF_PLACEHOLDER, abs(t))
    crit = invttail(DF_PLACEHOLDER, 0.025)
    st_matrix("_coefs", (b, se, t, p, b - crit*se, b + crit*se))
""" + _COLSTRIPE_MATA + "end\n"

_MATA_COEF_TABLE_Z = r"""
mata:
    b = st_matrix("e(b)")'
    se = sqrt(diagonal(st_matrix("e(V)")))
    t = b :/ se
    p = 2*normal(-abs(t))
    crit = invnormal(0.975)
    st_matrix("_coefs", (b, se, t, p, b - crit*se, b + crit*se))
""" + _COLSTRIPE_MATA + "end\n"


def _coef_table_mata(df_r: float | None) -> str:
    """按 df_r 是否为 None 选 t 分布或 z 分布的 Mata 代码。

    df_r 是回归误差自由度（e(df_r)）：regress/areg 等有（t 分布），
    logit/probit/poisson 等无（z 分布）。df_r 由 Python 侧直读内存，
    None 表示缺失 → z 分布。
    """
    if df_r is None:
        return _MATA_COEF_TABLE_Z
    return _MATA_COEF_TABLE_T.replace("DF_PLACEHOLDER", repr(float(df_r)))

def _at(row, idx):
    """取矩阵行元素并归一缺失；越界（矩阵形状异常）返回 None。"""
    if row is None or idx >= len(row):
        return None
    return _num(row[idx])


def parse_coef_table(ctx) -> dict:
    """通用系数提取：对任何 e-class 估计命令（e(b)/e(V) 存在）产出系数表。

    产出 ``{cmd, depvar, N, r2|r2_p|chi2, coefs[]}``；拟合优度键名按
    "实际取到哪个"填（regress→r2，logit→r2_p，ivregress→chi2）。
    """
    backend = _backend(ctx)

    # 1) 让后端跑 Mata，算好的系数表落在 Stata 内存矩阵 _coefs。
    #    t/z 分布由 Python 侧读 e(df_r) 决定（见 _coef_table_mata）。
    df_r = _get_e_scalar("df_r")
    backend.execute(_coef_table_mata(df_r))

    # 2) 直读内存：列名来自 e(b)（优先 colstripe 带前缀的完整名），
    #    数值来自 _coefs（行序一一对应）。
    from sfi import Macro, Matrix

    cmd = (Macro.getGlobal("e(cmd)") or "").strip().lower()
    depvar = (Macro.getGlobal("e(depvar)") or "").strip() or None
    # colstripe 组合名（Mata 写入 _mcp_colnames，| 分隔）；读不到回退 getColNames。
    names_raw = Macro.getGlobal("_mcp_colnames") or ""
    if names_raw:
        names = names_raw.split("|")
    else:
        names = Matrix.getColNames("e(b)") or []
    rows = Matrix.get("_coefs") or []

    coefs = []
    for i, name in enumerate(names):
        row = rows[i] if i < len(rows) else []
        coefs.append(
            {
                "var": name,
                "coef": _at(row, 0),
                "se": _at(row, 1),
                "t": _at(row, 2),
                "p": _at(row, 3),
                "ci": [_at(row, 4), _at(row, 5)],
            }
        )

    out: dict = {"cmd": cmd, "coefs": coefs}
    if depvar:
        out["depvar"] = depvar

    n = _get_e_scalar("N")
    if n is not None:
        out["N"] = n

    # 拟合优度：r2 → r2_p → chi2，取到哪个键就叫哪个名字（键名按实际取到的填）。
    for fit_name in ("r2", "r2_p", "chi2"):
        v = _get_e_scalar(fit_name)
        if v is not None:
            out[fit_name] = v
            break

    # 3) 通用抓取 e() 全量标量（P8）：ll/aic/bic/df_r/df_m/p/F/converged 等
    #    模型统计量，每个命令集合不同但都是 e(name)=value，通用解析不枚举命令。
    #    ereturn list 是纯显示命令，不改变 e()/r() 状态。
    from .parser import parse_ereturn_scalars

    ereturn = backend.execute("ereturn list")
    scalars = parse_ereturn_scalars(ereturn.text)
    if scalars:
        out["scalars"] = scalars

    return out
