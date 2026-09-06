"""受限模式：拦截 shell 逃逸、文件删除、越权文件路径（P12 防有害文件注入）。

为什么需要它（批判性审视缺陷 5）：``stata_run`` 能执行任意 Stata 命令，若 agent
被 prompt 注入，即可用 ``shell``/``erase``/``use "C:\\敏感"`` 逃逸到文件系统，
绕过 ``stata_load_data`` 的路径审计。受限模式给 ``stata_run`` 一个可选的安全边界。

三层防护（按危险度）：
1. **shell 逃逸**：``shell`` / ``winexec`` / ``!`` —— 一旦能执行 OS 命令，一切
   文件限制失效，最高危；
2. **文件删除**：``erase`` / ``rm`` —— 破坏性；
3. **越权文件路径**：``use`` / ``import`` / ``save`` / ``cd`` / ``append`` /
   ``merge`` 命令里的文件路径，用 DataPathAuditor 审计（限授权目录）。

命令首词提取（防绕过）：逐行提取命令，先剥 ``capture``/``quietly``/``noisily``
前缀，再剥 ``by varlist:`` / ``bysort varlist:`` 前缀——这是 mcp-for-stata 的
bysort 绕过 bug 的正确修法（它 validator 自己再剥一遍前缀，漏了 bysort）。
"""
from __future__ import annotations

import re

from .data_path import DataPathAuditor

# shell 逃逸 + 文件删除（最高危，黑名单）
_DANGEROUS_COMMANDS = {"shell", "winexec", "erase", "rm"}

# 前缀剥离：capture / quietly / noisily / qui（可叠加）
_PREFIX_RE = re.compile(r"^(capture|noisily|quietly|qui)\s+", re.IGNORECASE)

# by varlist: / bysort varlist: 前缀（varlist 后必须跟冒号）
_BY_RE = re.compile(r"^by(?:sort)?\s+([^:]+):\s*", re.IGNORECASE)

# 危险命令词：整句内出现即拦（不只看行首——堵分号/`use x; shell` 绕过，审计 #4）
_DANGEROUS_RE = re.compile(
    r"(^|[\s;])(shell|winexec|erase|rm)(\s|$)", re.IGNORECASE
)

# 带文件路径的命令（首词判断 + 需判断的 import 子命令）
_FILE_COMMANDS = {
    "use", "save", "cd", "append", "merge",
    "import", "insheet", "infile", "copy",
}
# import 是两词：import delimited/excel/... 才算文件命令
_IMPORT_KINDS = {
    "delimited", "excel", "spss", "sasxport", "sav", "dbase", "haver",
    "infix", "infile", "fred", "freduse", "hdf5", "fixed",
}

# 引号包裹的文件路径（可含空格）
_QUOTED = re.compile(r'"([^"]+)"')
# 无引号路径 token（不含引号/空白/注释/续行）
_UNQUOTED = re.compile(r'^(\S+)$')


def _strip_comment(line: str) -> str:
    """去掉行内注释（// 与 /* */；行首 * 注释由拆段跳过）。"""
    line = line.split("//", 1)[0]
    line = line.split("/*", 1)[0]
    return line


def _strip_prefix(line: str) -> str:
    """剥 capture/quietly/noisily/qui 前缀，再剥 by varlist: 前缀。"""
    while True:
        m = _PREFIX_RE.match(line)
        if not m:
            break
        line = line[m.end():].lstrip()
    m = _BY_RE.match(line)
    if m:
        line = line[m.end():].lstrip()
    return line


def _split_statements(code: str) -> list[str]:
    """把代码拆成"命令段"列表：按换行，行内再按 ``;`` 拆（防 `use x; shell`）。

    Stata 默认以换行分隔命令；``;`` 仅在 ``#delimit ;`` 下分隔，但保守起见
    一律拆——多拆只会多检，不会漏检（审计 #4）。
    """
    out: list[str] = []
    for raw_line in code.splitlines():
        line = _strip_comment(raw_line).strip()
        if not line or line.startswith("*"):
            continue
        for seg in line.split(";"):
            seg = _strip_prefix(seg.strip())
            if seg:
                out.append(seg)
    return out


def _first_token(stmt: str) -> str:
    m = re.match(r"^(\S+)", stmt)
    return m.group(1).lower() if m else ""


def check_dangerous(code: str) -> tuple[bool, str]:
    """检测 shell 逃逸 / 文件删除（整句扫描，非仅行首）。返回 (allowed, reason)。"""
    for stmt in _split_statements(code):
        # shell 逃逸：! 在句首
        if stmt.startswith("!"):
            return False, "shell escape (!) is blocked in restricted mode"
        if _DANGEROUS_RE.search(stmt):
            return False, "shell/winexec/erase/rm are blocked in restricted mode"
    return True, ""


def _audit_one_path(path: str, auditor: DataPathAuditor) -> tuple[bool, str]:
    path = path.strip().strip('"')
    if not path:
        return True, ""
    if "://" in path:
        if not auditor.check_url(path):
            return False, f"URL '{path}' is not allowed in restricted mode"
    else:
        if not auditor.check_local_path(path):
            return False, (
                f"file path '{path}' is outside allowed data directories "
                "in restricted mode"
            )
    return True, ""


def check_file_paths(code: str, auditor: DataPathAuditor) -> tuple[bool, str]:
    """检测文件类命令的路径是否越权（带引号或无引号都审计，审计 #4）。"""
    for stmt in _split_statements(code):
        cmd = _first_token(stmt)
        rest = stmt[len(cmd):].strip()
        if cmd not in _FILE_COMMANDS and cmd != "import":
            continue
        if cmd == "import":
            # import <kind> <path>：kind 必须是已知文件格式
            if not rest:
                continue
            kind = _first_token(rest)
            if kind not in _IMPORT_KINDS:
                continue  # import 非文件（如 import idcode）不管
            rest = rest[len(kind):].strip()
        if not rest:
            continue
        # 取第一个参数：优先引号路径，否则无引号路径 token
        m = _QUOTED.match(rest)
        if m:
            ok, reason = _audit_one_path(m.group(1), auditor)
            if not ok:
                return False, reason
            continue
        # 无引号：整句非空时的首 token 当路径候选（use auto.dta / save out）
        cand = _first_token(rest)
        if cand and _UNQUOTED.match(cand):
            ok, reason = _audit_one_path(cand, auditor)
            if not ok:
                return False, reason
    return True, ""


def restrict(code: str, auditor: DataPathAuditor) -> tuple[bool, str]:
    """受限模式总闸：先查危险命令，再查越权路径。返回 (allowed, reason)。"""
    ok, reason = check_dangerous(code)
    if not ok:
        return False, reason
    ok, reason = check_file_paths(code, auditor)
    if not ok:
        return False, reason
    return True, ""
