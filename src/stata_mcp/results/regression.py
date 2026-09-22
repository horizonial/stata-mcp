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

import math
import os
import re
import tempfile
from pathlib import Path

from .parser import _backend, _get_e_scalar, _num

GENERIC_RESULT_SOURCE_CAPABILITY = {
    "capability_id": "stata.generic-result-source.v1",
    "capability_version": 1,
    "snapshot_schema_version": "stata.generic-result.snapshot/v1",
    "extractor_contract_hash": (
        "320d520badfe130782be6f85832f8e797687259d671172e49a2a5d12ed62d7e0"
    ),
}


def _reghdfe_environment(backend) -> dict | None:
    observed = backend.execute("which reghdfe")
    if observed.rc != 0:
        return None
    path = None
    version = None
    for line in observed.text.splitlines():
        stripped = line.strip()
        if stripped.lower().endswith("reghdfe.ado"):
            path = stripped
        match = re.search(r"\bversion\s+([^\s]+)", stripped, flags=re.IGNORECASE)
        if match is not None:
            version = match.group(1)
    if not path or not version:
        return None
    return {"dependency": "reghdfe", "path": path, "version": version}


def _ivregress_first_stage(backend, endogenous: list[str]) -> list[dict] | None:
    observed = backend.execute("quietly estat firststage")
    if observed.rc != 0:
        return None
    from sfi import Matrix

    rows = Matrix.get("r(singleresults)") or []
    if len(rows) != len(endogenous):
        return None
    names = (
        "r2",
        "adjusted_r2",
        "partial_r2",
        "f_statistic",
        "df1",
        "df2",
        "p_value",
        "shea_partial_r2",
    )
    result = []
    for row_index, (variable, row) in enumerate(zip(endogenous, rows, strict=True)):
        values = {name: _num(row[index]) for index, name in enumerate(names)}
        required = ("partial_r2", "f_statistic", "df1", "df2", "p_value")
        if any(values[name] is None for name in required):
            return None
        result.append(
            {
                "endogenous_variable": variable,
                "statistics": values,
                "source": {
                    "locator_type": "R_MATRIX_ROW",
                    "matrix": "r(singleresults)",
                    "row_index": row_index + 1,
                    "column_semantics": list(names),
                    "producing_command": "estat firststage",
                },
            }
        )
    return result


def _estimation_sample_manifest(backend) -> dict | None:
    """从真实 ``e(sample)`` 捕获 observation-order bitset；helper 结束后清理变量。

    bitset 只是样本成员身份，不由 Python 重新计算统计量。大型样本在产品层应将
    payload 捕获为 Artifact；MCP 返回候选内容和 hash，Profile 决定如何固化。
    """
    import hashlib
    import uuid

    temp_name = "_mcp_s_" + uuid.uuid4().hex[:16]
    descriptor, raw_path = tempfile.mkstemp(
        prefix="stata-mcp-esample-", suffix=".csv"
    )
    os.close(descriptor)
    export_path = Path(raw_path)
    created = backend.execute(f"quietly generate byte {temp_name} = e(sample)")
    if created.rc != 0:
        export_path.unlink(missing_ok=True)
        return None
    try:
        # Never bulk-read e(sample) through SFI here.  Two independent PyStata engines doing
        # repeated concurrent Data.get transfers can terminate one native process without a
        # Python exception.  Ask Stata itself to materialize the one-column sample vector, then
        # package that Stata-produced payload in Python.  This preserves the statistical source
        # of truth while avoiding the unstable cross-engine FFI path.
        escaped_path = export_path.as_posix().replace('"', '""')
        exported = backend.execute(
            f'quietly export delimited {temp_name} using "{escaped_path}", '
            "novarnames replace"
        )
        if exported.rc != 0:
            return None
        values = [
            line.strip().strip('"')
            for line in export_path.read_text(encoding="utf-8-sig").splitlines()
            if line.strip()
        ]
        nobs = len(values)
        if nobs == 0:
            return None
        bits = bytearray((nobs + 7) // 8)
        included = 0
        for obs, value in enumerate(values):
            if value not in {"0", "0.0", ".", ""}:
                bits[obs // 8] |= 1 << (obs % 8)
                included += 1
        payload = bytes(bits)
        return {
            "schema_version": "stata.estimation-sample-mask/v1alpha1",
            "source": "e(sample)",
            "row_domain": "observation_order",
            "row_count": nobs,
            "included_count": included,
            "encoding": "bitset_lsb0_hex",
            "mask_hex": payload.hex(),
            "mask_sha256": hashlib.sha256(payload).hexdigest(),
        }
    finally:
        backend.execute(f"quietly drop {temp_name}")
        export_path.unlink(missing_ok=True)

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
    st_global("_mcp_eqnames", invtokens(S[,1]', "|"))
    st_global("_mcp_termnames", invtokens(S[,2]', "|"))
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
    eq_names = (Macro.getGlobal("_mcp_eqnames") or "").split("|")
    term_names = (Macro.getGlobal("_mcp_termnames") or "").split("|")
    if len(eq_names) != len(names):
        eq_names = [""] * len(names)
    if len(term_names) != len(names):
        term_names = list(names)
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

    # Stata 原生来源地址由执行侧返回，避免上层根据展示名猜 e(b)/e(V) 位置。
    # 这里只陈述 primitive stored-result locator；SE/p/CI 的不可变 Derivation
    # Receipt 由已激活 Result Profile 在上层 Finalization 时创建。
    term_sources = []
    for i, display_key in enumerate(names):
        equation = eq_names[i]
        term = term_names[i]
        term_sources.append(
            {
                "display_key": display_key,
                "equation": equation,
                "term": term,
                "coefficient": {
                    "locator_type": "E_MATRIX_CELL",
                    "matrix": "e(b)",
                    "equation": equation,
                    "column_key": term,
                },
                "variance": {
                    "locator_type": "E_MATRIX_CELL",
                    "matrix": "e(V)",
                    "row_equation": equation,
                    "row_key": term,
                    "column_equation": equation,
                    "column_key": term,
                },
            }
        )

    def e_scalar(name: str) -> dict:
        return {"locator_type": "E_SCALAR", "name": f"e({name})"}

    def e_macro(name: str) -> dict:
        return {"locator_type": "E_MACRO", "name": f"e({name})"}

    cmdline = (Macro.getGlobal("e(cmdline)") or "").strip() or None
    vce = (Macro.getGlobal("e(vce)") or "").strip() or None
    vcetype = (Macro.getGlobal("e(vcetype)") or "").strip() or None
    if cmdline:
        out["cmdline"] = cmdline
    if vce:
        out["vce"] = vce
    if vcetype:
        out["vcetype"] = vcetype
    scalar_sources = {"N": e_scalar("N")}
    if cmd in {"regress", "reghdfe"}:
        scalar_sources.update({"r2": e_scalar("r2"), "df_r": e_scalar("df_r")})
    elif cmd == "logit":
        scalar_sources["r2_p"] = e_scalar("r2_p")
    elif cmd == "ivregress":
        scalar_sources["r2"] = e_scalar("r2")
    macro_sources = {
        "cmd": e_macro("cmd"),
        "cmdline": e_macro("cmdline"),
        "depvar": e_macro("depvar"),
        "vce": e_macro("vce"),
        "vcetype": e_macro("vcetype"),
    }
    if cmd == "reghdfe":
        scalar_sources["r2_within"] = e_scalar("r2_within")
        macro_sources["absvars"] = e_macro("absvars")
        macro_sources["clustvar"] = e_macro("clustvar")
    elif cmd == "ivregress":
        macro_sources["estimator"] = e_macro("estimator")
        macro_sources["endog"] = e_macro("endog")
        macro_sources["exog"] = e_macro("exog")
        macro_sources["exogr"] = e_macro("exogr")
    out["stored_result_source_map"] = {
        "schema_version": "stata.stored-result-source-map/v1alpha1",
        "command_type": cmd,
        "terms": term_sources,
        "scalars": scalar_sources,
        "macros": macro_sources,
    }
    sample_manifest = _estimation_sample_manifest(backend)
    if sample_manifest is not None:
        out["estimation_sample_manifest"] = sample_manifest
    if cmd == "reghdfe":
        absvars = (Macro.getGlobal("e(absvars)") or "").strip()
        clustvar = (Macro.getGlobal("e(clustvar)") or "").strip()
        if absvars:
            out["absorbed_effects"] = absvars.split()
        out["cluster_variables"] = clustvar.split() if clustvar else []
        r2_within = _get_e_scalar("r2_within")
        if r2_within is not None:
            out["r2_within"] = r2_within
        environment = _reghdfe_environment(backend)
        if environment is not None:
            out["profile_environment"] = environment
    elif cmd == "ivregress":
        estimator = (Macro.getGlobal("e(estimator)") or "").strip().lower()
        endogenous = (Macro.getGlobal("e(endog)") or "").strip().split()
        included = (Macro.getGlobal("e(exogr)") or "").strip().split()
        all_instruments = (Macro.getGlobal("e(exog)") or "").strip().split()
        included_set = set(included)
        excluded = [item for item in all_instruments if item not in included_set]
        first_stage = _ivregress_first_stage(backend, endogenous)
        if estimator:
            out["iv_estimator"] = estimator
        out["endogenous_variables"] = endogenous
        out["included_exogenous_variables"] = included
        out["excluded_instruments"] = excluded
        if first_stage is not None:
            out["first_stage"] = first_stage

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
    # The display parser is best-effort: Stata can occasionally return an empty
    # display payload even though the e() values remain present in memory.  The
    # registered Result Profile relies on these primitive stored results, so
    # capture its required scalar subset through SFI as the authoritative path.
    for scalar_name in ("N", "r2", "r2_p", "df_r", "r2_within"):
        scalar_value = _get_e_scalar(scalar_name)
        if scalar_value is not None:
            scalars[scalar_name] = scalar_value
    if scalars:
        out["scalars"] = scalars

    # The formal boundary is source provenance, not a method whitelist.  Publish one generic
    # catalog for every e-class result.  Higher layers may select any catalog element but cannot
    # submit a value that Stata did not return here.
    scalar_sources.update({name: e_scalar(name) for name in scalars})
    out["stored_result_source_map"]["scalars"] = scalar_sources
    catalog: list[dict] = []
    source_by_term = {item["display_key"]: item for item in term_sources}

    def add_catalog(
        source_key: str,
        value,
        statistic_kind: str,
        locator: dict,
        primitive_locators: list[dict] | None = None,
    ) -> None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return
        if not math.isfinite(number):
            return
        catalog.append(
            {
                "source_key": source_key,
                "value": number,
                "statistic_kind": statistic_kind,
                "locator": locator,
                "primitive_locators": primitive_locators or [],
            }
        )

    for name, value in sorted(scalars.items()):
        add_catalog(
            f"scalar.{name}", value, "stata_returned_scalar", e_scalar(name)
        )
    for coefficient in coefs:
        term = str(coefficient["var"])
        source = source_by_term[term]
        coefficient_locator = source["coefficient"]
        variance_locator = source["variance"]
        add_catalog(
            f"term.{term}.coefficient",
            coefficient.get("coef"),
            "coefficient",
            coefficient_locator,
        )
        derived = {
            "se": (coefficient.get("se"), "standard_error"),
            "statistic": (coefficient.get("t"), "test_statistic"),
            "p": (coefficient.get("p"), "p_value"),
            "ci95.lower": ((coefficient.get("ci") or [None, None])[0], "confidence_interval_lower"),
            "ci95.upper": ((coefficient.get("ci") or [None, None])[1], "confidence_interval_upper"),
        }
        for suffix, (value, kind) in derived.items():
            add_catalog(
                f"term.{term}.{suffix}",
                value,
                kind,
                {
                    "locator_type": "TRUSTED_STATA_DERIVATION_RECEIPT",
                    "source_key": f"term.{term}.{suffix}",
                },
                [coefficient_locator, variance_locator],
            )
    out["result_source_capability"] = dict(GENERIC_RESULT_SOURCE_CAPABILITY)
    out["result_catalog"] = {
        "schema_version": "stata.result-catalog/v1",
        "elements": catalog,
    }

    return out
