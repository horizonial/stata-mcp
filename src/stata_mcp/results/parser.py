"""ResultParser 协议 + 注册表 + 路由主函数（ARCHITECTURE.md §6.5，P3）。

设计（直接采用 DESIGN §8 已验证结论，不重新验证）：
- 路由依据内存里的 ``e(cmd)`` 宏，完全不解析输出文本：只要引擎内存还有
  e() 结果就能结构化，绕开 SMCL / 表格边框的文本解析坑。
- sfi（pystata 的内存接口）一律在函数体内懒加载：模块 import 阶段不碰
  pystata，也就不占 license / 不触发引擎点火（硬约束：能 import）。
"""
from __future__ import annotations

from typing import Protocol

# 命令名（e(cmd) 小写）→ 能解析它的 parser 实例。
# 由 results/*.py 模块顶部的 ``@register(...)`` 填充（工具注册表同款思路）。
PARSERS: dict[str, "ResultParser"] = {}


def register(command_types: tuple[str, ...]):
    """把 parser 类注册到它声明的每个命令名上。

    用法（见 results/regression.py）::

        @register(("regress", "areg", ...))
        class RegressionParser:
            command_types = ("regress", "areg", ...)
            def parse(self, ctx) -> dict: ...

    同一个 parser 实例覆盖多个命令；重复注册在 import 期即报错。
    """

    def decorator(parser_cls: type["ResultParser"]) -> type["ResultParser"]:
        parser = parser_cls()
        for cmd in command_types:
            if cmd in PARSERS:
                raise ValueError(f"duplicate parser registration: {cmd!r}")
            PARSERS[cmd] = parser
        return parser_cls

    return decorator


class ResultParser(Protocol):
    """估计类命令的结果解析器（ARCHITECTURE.md §6.5）。

    ``command_types``：声明覆盖的 e(cmd) 命令名。
    ``parse(ctx)``：P3 的 ctx 即执行后端 backend；未来可换成携带 ``.backend``
    的 Session 上下文。返回可 JSON 序列化的 dict，键按实际取到填、不塞 None。
    """

    command_types: tuple[str, ...]

    def parse(self, ctx) -> dict: ...


def _backend(ctx):
    """从 ctx 解出执行后端：兼容"直接传 backend"与"传带 .backend 的上下文"。"""
    return getattr(ctx, "backend", ctx)


# ---- sfi 直读辅助 -----------------------------------------------------------
# 统一 try/except + 缺失归一：引擎没点火 / 非 pystata 后端 / 统计量取不到时，
# 一律返回 None / 空串，由调用方省略键或判"不是估计命令"，不让异常冒泡到上层。


def _get_e_cmd() -> str:
    """读当前 e(cmd)。空串 = 当前没有估计结果（DESIGN §8 的路由判据）。"""
    try:
        from sfi import Macro

        cmd = Macro.getGlobal("e(cmd)")
    except Exception:
        return ""
    return (cmd or "").strip().lower()


def _num(value) -> float | None:
    """把 sfi 数值归一成 float；缺失(.)/None/nan/inf 一律归一为 None。

    sfi 可能把 Stata 缺失值映成 None 或超大 double，这里统一滤掉，
    保证上层"取不到就省略键、不塞假的 None 值"语义成立。
    """
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f or f == float("inf") or f == float("-inf"):  # nan / inf
        return None
    if f > 1e15:  # Stata 缺失(.) 约为 8.99e307；真实统计量不可能超过 1e15
        return None
    return f


def _get_e_scalar(name: str) -> float | None:
    """读 e(name) 数值标量；不存在 / 缺失返回 None。"""
    try:
        from sfi import Scalar

        return _num(Scalar.getValue(f"e({name})"))
    except Exception:
        return None


def _fallback(cmd: str) -> dict:
    """最小兜底结构：有 e(cmd) 但没有专门 parser 时，也给 agent 最少的结构。"""
    out: dict = {"cmd": cmd}
    n = _get_e_scalar("N")
    if n is not None:
        out["N"] = n
    return out


def _has_eb() -> bool:
    """当前 e() 是否有可读的 1×k 系数向量（= 产出系数的估计命令）。

    P8 泛化依据（spike45 实测）：所有 e-class 估计命令的 e(b) 都是 1×k 系数
    向量，这是 Stata 的统一约定。用它替代"命令名枚举"，任何产出 e(b) 的命令
    都能走通用系数提取，无需逐个登记命令名。

    P11 加固：额外校验 e(b) 是 **1×k 行向量**（len(m)==1）。若某命令的 e(b)
    是多行矩阵（形状异常），通用系数提取的转置/对齐假设会失效，可能"悄悄给
    错数字"——比报错更危险。因此这里 fail-closed：非 1×k 一律视为不可提取，
    走 fallback（{cmd, N}）而非硬提取。
    """
    try:
        from sfi import Matrix

        m = Matrix.get("e(b)")
        return bool(m) and len(m) == 1
    except Exception:
        return False


def parse_ereturn_scalars(text: str) -> dict:
    """通用解析 ``ereturn list`` 输出的 scalars 段，返回 ``{name: value}``。

    P8 依据：模型的"非系数参数"（ll/aic/bic/df_r/df_m/p/F/chi2/converged/…）
    都是 e() 里的命名标量，每个命令集合不同但格式统一（``e(name) = value``）。
    通用解析即可，**不枚举命令**。数值归一复用 ``_num``（缺失 . / nan / 巨值
    归一为省略）。
    """
    import re

    scalar_re = re.compile(r"e\(([A-Za-z0-9_]+)\)\s*=\s*(.+)$")
    scalars: dict = {}
    section: str | None = None
    for line in text.splitlines():
        s = line.strip()
        low = s.lower()
        if low == "scalars:":
            section = "scalars"
            continue
        if section == "scalars" and low in ("macros:", "matrices:", "functions:"):
            break
        if section != "scalars":
            continue
        m = scalar_re.match(s)
        if m:
            raw = m.group(2).strip()
            if raw in ("", "."):
                continue
            v = _num(raw)
            if v is not None:
                scalars[m.group(1)] = v
    return scalars


def try_parse(backend) -> dict | None:
    """路由主函数：专用 parser 优先，否则 e(b) 存在走通用系数提取。

    三级路由（P8 泛化后）：
    1. PARSERS 里有该 e(cmd) 的专用 parser → 用它（未来特殊结构：方差分解、
       混合模型方差分量等，注册即覆盖通用路径）；
    2. 否则 e(b) 可读 → 通用系数提取（regression.parse_coef_table），
       **不枚举命令名**，覆盖任意 e-class 估计命令；
    3. 否则（e(cmd) 存在但无 e(b)，如个别特殊命令）→ 最小兜底 {cmd, N}。

    返回 None 表示"当前没有估计结果可结构化"。parser 内部异常不在此吞掉，
    交由调用方（tools/run.py）统一 catch，降级为 structured=None。
    """
    cmd = _get_e_cmd()
    if not cmd:
        return None
    parser = PARSERS.get(cmd)
    if parser is not None:
        return parser.parse(backend)
    if _has_eb():
        from .regression import parse_coef_table  # 延迟 import 避免循环

        return parse_coef_table(backend)
    return _fallback(cmd)


# ---- 会话快照（worker 内调用，P10a 会话隔离） -------------------------------

_RETURN_SCALAR_RE = None  # 惰性编译


def _parse_return_list(text: str) -> dict:
    """解析 ``return list`` 输出 → {"r_scalars": {...}, "r_macros": {...}}。

    worker 内可复用（worker 进程内 r()/e() 才可读，见 worker.py）。
    """
    global _RETURN_SCALAR_RE
    import re

    if _RETURN_SCALAR_RE is None:
        _RETURN_SCALAR_RE = re.compile(r"^\s*r\(([A-Za-z0-9_]+)\)\s*=\s*(.+?)\s*$")
        _RETURN_MACRO_RE = re.compile(r"^\s*r\(([A-Za-z0-9_]+)\)\s*:\s*(.*?)\s*$")
        globals()["_RETURN_MACRO_RE"] = _RETURN_MACRO_RE

    scalar_re = _RETURN_SCALAR_RE
    macro_re = globals()["_RETURN_MACRO_RE"]
    sections = ("scalars", "macros", "matrices", "functions")
    scalars: dict = {}
    macros: dict = {}
    section = None
    for line in text.splitlines():
        s = line.strip()
        if s.endswith(":") and s[:-1].strip().lower() in sections:
            section = s[:-1].strip().lower()
            continue
        if section == "scalars":
            m = scalar_re.match(line)
            if m:
                v = _num(m.group(2))
                if v is not None:
                    scalars[m.group(1)] = v
        elif section == "macros":
            m = macro_re.match(line)
            if m:
                macros[m.group(1)] = m.group(2).strip().strip('"')

    out: dict = {}
    if scalars:
        out["r_scalars"] = scalars
    if macros:
        out["r_macros"] = macros
    return out


def snapshot_state(backend) -> dict:
    """worker 内只读当前会话状态，返回 {structured, r_scalars, r_macros, shape}。

    顺序注意：先读 r()（return list）再读 e()（try_parse）——try_parse 内部会跑
    Mata，Mata 会覆盖 r()，所以 r() 必须在这之前读。
    """
    out: dict = {}
    try:
        r = backend.execute("return list")
        parsed = _parse_return_list(r.text)
        if "r_scalars" in parsed:
            out["r_scalars"] = parsed["r_scalars"]
        if "r_macros" in parsed:
            out["r_macros"] = parsed["r_macros"]
    except Exception:
        pass

    try:
        structured = try_parse(backend)
        if structured:
            out["structured"] = structured
    except Exception:
        pass

    try:
        backend.execute("scalar _snap_N = _N\nscalar _snap_k = c(k)")
        from sfi import Scalar

        n = _num(Scalar.getValue("_snap_N"))
        k = _num(Scalar.getValue("_snap_k"))
        if n is not None and k is not None:
            out["shape"] = {"N": int(n), "k": int(k)}
    except Exception:
        pass

    try:
        from sfi import Data

        nv = Data.getVarCount()
        names = [Data.getVarName(i) for i in range(nv)]
        if names:
            out["variables"] = names
    except Exception:
        pass

    return out


def read_rows(backend, n: int) -> dict:
    """读当前数据集前 n 行（P1b data_rows）。worker 内 sfi.Data 直读，内存受限。

    产出 ``{N, variables, rows}``；rows 为二维数组（每行 = 各变量值），字符串
    过长截断、缺失值映射为 None。读取失败返回空 dict。
    """
    try:
        from sfi import Data

        nvar = Data.getVarCount()
        if nvar == 0:
            return {}
        names = [Data.getVarName(i) for i in range(nvar)]
        nobs = Data.getObsTotal()
        n_read = min(int(n), nobs)
        rows = []
        for obs in range(n_read):
            row = []
            for v in range(nvar):
                # Data.getAt(var, obs) 返回标量（实测；Data.get 返回嵌套列表）。
                val = Data.getAt(v, obs)
                row.append(_cell(val))
            rows.append(row)
        return {"N": nobs, "n_rows": n_read, "variables": names, "rows": rows}
    except Exception:
        return {}


def _cell(val):
    """把单元格值归一成 JSON 安全形式：缺失/巨值→None，长字符串截断，bytes→解码。"""
    if val is None:
        return None
    if isinstance(val, (bytes, bytearray)):
        try:
            val = val.decode("utf-8", "replace")
        except Exception:
            val = None
    if isinstance(val, float):
        f = val
        if f != f or f in (float("inf"), float("-inf")) or abs(f) > 1e15:
            return None
        return int(f) if f.is_integer() else f
    if isinstance(val, str) and len(val) > 200:
        return val[:200] + "..."
    return val
