"""受限模式（P12 + P16b 补强）：拦截 shell 逃逸、文件删除、外部代码、越权文件路径。

为什么需要它：``stata_run`` 能执行任意 Stata 命令，若 agent 被 prompt 注入，可用
``shell``/``erase``/``use "C:\\敏感"`` 逃逸到文件系统。受限模式给 ``stata_run``
一个可选安全边界。

**边界声明（重要）**：受限模式是**静态尽力而为 + fail-closed** 的防线，不是完备的
沙箱。它无法对抗全部 Stata 语法（宏求值、间接命令等）。对真正不可信的代码，正确
姿势是"根本不放行"，受限模式只挡常见注入路径。P16b 已补强：续行合并、``using``
关键字、saveold/export/copy/do/run/include、宏/复合引号 fail-closed、URL 拒绝。

拦截清单：
- 危险词（整句词边界）：shell / winexec / erase / rm（OS 逃逸与删除）
- 命令位 do / run / include（执行外部代码 = 逃逸通道）
- 行首 ``!`` shell 转义
- 路径命令读写授权目录外（含 copy 源与目的、export 目标）
- 含宏 ``$`` / 复合引号反引号 的路径（fail-closed）
- URL（``://``）——受限模式不允许网络访问
"""
from __future__ import annotations

import re

from .data_path import DataPathAuditor

# 危险词：出现在任何位置的整句词边界（不匹配引号内 / 变量名子串）
_DANGEROUS_RE = re.compile(r"(^|[\s;])(shell|winexec|erase|rm)(\s|$)", re.IGNORECASE)

# 前缀剥离：capture / quietly / noisily / qui（可叠加）
_PREFIX_RE = re.compile(r"^(capture|noisily|quietly|qui)\s+", re.IGNORECASE)
# by varlist: / bysort varlist: 前缀
_BY_RE = re.compile(r"^by(?:sort)?\s+([^:]+):\s*", re.IGNORECASE)

# 命令位才算"外部代码执行/删除"的词（避免误伤变量名 run/do/include）
_EXTERNAL_CMDS = {"do", "run", "include", "shell", "winexec", "erase", "rm"}

# 路径类命令（读/写/切目录）：统一做路径审计。
# import / export / log / copy 是"动词+子命令[using] 路径"。
_FILE_COMMANDS = {
    "use", "insheet", "infile", "append", "merge", "save", "saveold",
    "copy", "cd", "type", "log", "import", "export",
}
# import/export 的已知子命令（文件格式），其他 import(如 import idcode)不管
_FILE_KINDS = {
    "delimited", "excel", "spss", "sasxport", "sav", "dbase", "haver",
    "infix", "infile", "fixed", "fred", "hdf5", "graph", "icd9",
    "icd10", "sas", "strata", "replace", "xlsx", "psytab",
}
# 不是路径参数的常见关键字（跳过，避免误当路径）
_KEYWORDS = {
    "using", "replace", "if", "in", "casewise", "clear", "firstrow",
    "sheet", "no", "keep", "ren", "destring", "name", "as",
}

_QUOTED = re.compile(r'"([^"]*)"')
_STRINGLITERAL = re.compile(r'"([^"]*)"')


def _merge_continuations(code: str) -> str:
    """合并 ``///`` 续行：``///`` 后内容拼到下一行（否则换行会把路径/命令拆断）。"""
    lines = code.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # /// 续行：把续行号的行首注释后的部分接到当前行
        if "///" in line:
            head, _, tail = line.partition("///")
            # 收集所有以续行符号结尾的后续行内容
            j = i + 1
            pieces = []
            while j < len(lines) and (j == i + 1 or lines[j - 1].count("///") or True):
                # 简单处理：只把下一行非 * 注释的内容并进来，若下一行也 /// 继续
                nxt = lines[j]
                if nxt.strip().startswith("*"):
                    j += 1
                    continue
                pieces.append(nxt)
                j += 1
                if "///" not in nxt:
                    break
            out.append(head + " " + " ".join(p for p in pieces if not p.strip().startswith("*")))
            i = j
        else:
            out.append(line)
            i += 1
    return "\n".join(out)


def _strip_comment(line: str) -> str:
    """去行内注释 // 与 /* ... */（行首 * 由拆段跳过）。

    ``://``（URL）里的 ``//`` 不算注释——用 ``(?<!:)//`` 匹配真正的注释起点，
    否则 URL 会被切坏导致漏检（P16b：`use "https://evil"` 必须能审计）。
    """
    m = re.search(r"(?<!:)//", line)
    if m:
        line = line[: m.start()]
    line = re.split(r"/\*.*?\*/", line, flags=re.DOTALL)[0]
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
    """把代码拆成"命令段"：合并续行 → 按换行 → 行内按 ``;`` 拆。"""
    code = _merge_continuations(code)
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


def _has_macro_danger(path: str) -> bool:
    """路径含宏/复合引号/变量拼接 → restricted 下 fail-closed（P16b）。"""
    return "$" in path or "`" in path


def check_dangerous(code: str) -> tuple[bool, str]:
    """检测 OS 逃逸 / 删除 / 外部代码执行。返回 (allowed, reason)。"""
    for stmt in _split_statements(code):
        if stmt.startswith("!"):
            return False, "shell escape (!) is blocked in restricted mode"
        if _DANGEROUS_RE.search(stmt):
            return False, "shell/winexec/erase/rm are blocked in restricted mode"
        cmd = _first_token(stmt)
        if cmd in _EXTERNAL_CMDS:
            return False, f"command '{cmd}' (external code/delete) is blocked in restricted mode"
    return True, ""


def _tokens(stmt: str) -> list[str]:
    """切 token：引号字符串整体算一个，其余按空白。返回原始 token 列表。"""
    toks: list[str] = []
    i = 0
    n = len(stmt)
    while i < n:
        c = stmt[i]
        if c == '"':
            m = _STRINGLITERAL.match(stmt[i:])
            if m:
                toks.append(m.group(0))
                i += m.end()
                continue
            i += 1
            continue
        if c.isspace():
            i += 1
            continue
        j = i
        while j < n and not stmt[j].isspace():
            j += 1
        toks.append(stmt[i:j])
        i = j
    return toks


def _audit_one_path(path: str, auditor: DataPathAuditor) -> tuple[bool, str]:
    path = path.strip().strip('"')
    if not path:
        return True, ""
    if "://" in path:
        return False, "URL/network access is not allowed in restricted mode"
    if _has_macro_danger(path):
        return False, "macro/compound-quote in path is not allowed in restricted mode"
    if not auditor.check_local_path(path):
        return False, (
            f"file path '{path}' is outside allowed data directories in restricted mode"
        )
    return True, ""


def check_file_paths(code: str, auditor: DataPathAuditor) -> tuple[bool, str]:
    """审计路径类命令的文件路径是否越权（P16b：using/多词/saveold/export/copy/宏）。"""
    for stmt in _split_statements(code):
        toks = _tokens(stmt)
        if not toks:
            continue
        verb = _first_token(toks[0])
        rest = toks[1:]
        if verb not in _FILE_COMMANDS:
            continue

        # import/export/log/copy 是动词+子命令；确定"是否文件命令 + 有效负载 token"
        payload: list[str] = []
        if verb in ("import", "export"):
            if not rest:
                continue
            kind = _first_token(rest[0])
            if kind not in _FILE_KINDS:
                continue  # 非文件的 import/export
            payload = rest[1:]
        elif verb == "copy":
            payload = rest  # copy 源 + 目标都要审（任一越权即拒）
        else:
            payload = rest

        # 逐 payload token：跳过纯关键字
        for tok in payload:
            low = tok.strip('"').lower()
            if low in _KEYWORDS or not tok.strip():
                continue
            # 引号字符串必然是路径候选
            if tok.startswith('"'):
                ok, reason = _audit_one_path(tok[1:-1], auditor)
                if not ok:
                    return False, reason
                continue
            # 裸 token：命令位/含路径特征才算候选，避免把变量名当路径误伤
            is_bare_filename = any(ch in tok for ch in "./\\") or low.endswith(
                (".dta", ".csv", ".xlsx", ".do", ".dat", ".txt", ".log", ".gph")
            )
            if is_bare_filename:
                ok, reason = _audit_one_path(tok, auditor)
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
