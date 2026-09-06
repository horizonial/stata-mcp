"""L1 参数校验：白名单正则纯函数（ARCHITECTURE.md §4 L1，P4）。

为什么用"白名单正则"而不是黑名单/转义：
- 变量名/包名这类 token 会被拼进 Stata 命令文本；白名单从字符集层面排除注入面
  （引号、空格、操作符、路径分隔符根本进不来），黑名单永远有漏。
- 本模块只做"结构合法性"判定：是否是合法 Stata 变量名、合法文件名等。
  保留字（`_b`/`if` 等 Stata 内部名）不属于结构问题，这里不拦（需要时再加表）。

这些是纯函数、无 IO：未来工具 import 后直接复用，P5+ 再统一成 validate 装饰器
（§4 说统一装饰器，但那需要会话上下文/可配置开关，本阶段只交判定函数本身）。
"""
from __future__ import annotations

import re

# Stata 变量名：字母或下划线开头，后接字母/数字/下划线，总长 1–32。
# 为什么允许下划线开头：Stata 的 _n/_N/_b 等系统量以下划线开头，规则上
# 用户变量也可（任务规约明确"字母或下划线开头"）。
_VARNAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,31}$")

# 宽松标识符（包名 / 结果名等非变量场景）：必须以字母开头（包/程序名不以
# 下划线开头——那会与系统 ado 名冲突），长度放宽到 64。
# 对比 varname：少了下划线开头、多了长度余量；两者都不放行引号/空格/分隔符。
_IDENT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")

# 文件名安全位：不允许出现在"单段文件名"里的字符。
# / \     路径分隔符（杜绝路径穿越入口）
# < > : "  Windows 保留字符
# | ? *    Windows 保留字符 + 通配符（? * 还防 glob 注入）
_UNSAFE_FILENAME_CHARS = set('/\\<>:"|?*')

# Windows 保留设备名：即使带扩展名（CON.txt）也不允许作为文件名。
_WINDOWS_DEVICE_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def is_valid_varname(s: str) -> bool:
    """是否合法 Stata 变量名（结构规则：字母/下划线开头，1–32，仅字母数字下划线）。"""
    return isinstance(s, str) and bool(_VARNAME_RE.match(s))


def is_valid_identifier(s: str) -> bool:
    """是否合法宽松标识符（用于包名等，非 Stata 变量名场景，最长 64、字母开头）。"""
    return isinstance(s, str) and bool(_IDENT_RE.match(s))


def validate_varname(s: str) -> None:
    """变量名校验：非法则 raise ``ValueError``（带清晰可读的报错）。

    非字符串属于编程错误，raise ``TypeError``，让调用方尽早发现。
    """
    if not isinstance(s, str):
        raise TypeError(f"variable name must be str, got {type(s).__name__}")
    if not is_valid_varname(s):
        raise ValueError(
            f"invalid Stata variable name {s!r}: 必须以字母或下划线开头，"
            "只含字母/数字/下划线，长度 1–32"
        )


def is_safe_filename(s: str) -> bool:
    """是否安全的"单段文件名"（不含路径）。

    拒绝：路径分隔符、``..``、通配符、Windows 保留字符、保留设备名、
    控制字符、以空格/点结尾（Windows 会剥离或歧义）。
    只用于叶子文件名；完整路径的归属判断交给 DataPathAuditor。
    """
    if not isinstance(s, str) or not s:
        return False
    if s in (".", ".."):
        return False
    if any(c in s for c in _UNSAFE_FILENAME_CHARS):
        return False
    # 控制字符（换行/NUL 等）不进文件名
    if any(ord(c) < 32 for c in s):
        return False
    # Windows：末尾空格/点会被剥离或产生歧义
    if s.rstrip() != s or s.rstrip(".") != s:
        return False
    # Windows 保留设备名：按第一个点前的 stem 判断（CON.dta 也非法）
    stem = s.split(".", 1)[0].upper()
    if stem in _WINDOWS_DEVICE_NAMES:
        return False
    return True


__all__ = [
    "is_valid_varname",
    "is_valid_identifier",
    "validate_varname",
    "is_safe_filename",
]
