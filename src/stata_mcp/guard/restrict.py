"""受限模式（P12–P16f）：白名单命令 + 路径审计的注入拦截器。**不是沙箱。**

为什么需要它：``stata_run`` 能执行任意 Stata 命令，若 agent 被 prompt 注入，可用
``shell``/``erase``/``use "C:\\敏感"`` 逃逸到文件系统。受限模式给 ``stata_run``
一道"挡住常见注入路径"的纵深防线。

**重要边界声明（P16f，诚实的定性）**：受限模式**不能声称是 fail-closed 沙箱**。
Stata 语法面极广（宏求值、``frame name: cmd`` 前缀、``table, command()`` 嵌套、
别名 lab/label、``#delimit``、大括号块…），静态文本解析封不完——任何基于
"解析任意 Stata 代码"的防线都有暗角（P0-P16f 已多次证明）。**对真正不可信的内容，
正确姿势是根本不提供自由 code 执行**（只用白名单结构化工具）；restricted 只用于
"代码基本可信、仅需防常见注入"的纵深。可复现的已知非沙箱入口见 tests + DESIGN。

当前拦截：
- 命令白名单（不在 _ALLOWED_VERBS 一律拒）；frame/table/import-fred-haver 等含
  嵌套命令/网络能力的命令从白名单移除；
- 危险词 shell/winexec/erase/rm、命令位 do/run/include、行首 ``!``；
- 路径命令读写授权目录外（use/append/merge/import/export/copy/saveold/describe-using 等）；
- 白名单内子命令文件写入（estimates/est save·use、label save、table export/dofile）；
- 含宏 ``$``/复合引号、URL 的网络访问一律拒。
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
    # describe/des ... using <file> 会读数据文件（P16f #3）
    "describe", "des",
}

# 命令白名单（P16c #2 fail-closed）：不在白名单的命令一律拒绝。
# 受限模式只允许"本地授权目录内的数据分析"，禁网络(webuse)、禁 OS 逃逸
# (python:/mata:/shell/filefilter/putexcel/outfile/...)、禁执行外部代码(do/run/include)、
# 禁任意图导出(graph export 需写外部文件)。含常见缩写（su/di/reg...）。
_ALLOWED_VERBS = {
    # 输出/环境
    "display", "di", "set", "macro", "scalar", "matrix", "return",
    "ereturn", "assert", "preserve", "restore", "clear",
    # 数据处理
    "gen", "generate", "g", "egen", "replace", "drop", "keep", "sort",
    "order", "rename", "label", "encode", "decode", "destring",
    "tostring", "recode", "tempvar", "tempfile", "capture", "by", "bysort",
    # 统计
    "summarize", "sum", "su", "describe", "des", "list", "li", "count",
    "codebook", "tabulate", "tab", "quietly", "qui", "noisily",
    "regress", "reg", "logit", "probit", "oprobit", "ologit", "mlogit",
    "poisson", "nbreg", "xtset", "xtreg", "xtdescribe", "xtsum", "areg",
    "ivregress", "tobit", "heckman", "test", "testparm", "lincom", "estimates",
    "est", "predict", "margins", "nestreg", "sureg",
    # 文件类并入白名单，但 P16g：use/infile/insheet 从 restricted 移除——
    # 它们的 varlist-using 语法路径解析不可靠；数据加载统一走 stata_load_data 工具。
} | (_FILE_COMMANDS - {"use", "infile", "insheet"})
# import/export 的已知子命令（文件格式），其他 import(如 import idcode)不管
_FILE_KINDS = {
    "delimited", "excel", "spss", "sasxport", "sav", "dbase",
    "infix", "infile", "fixed", "hdf5", "icd9",
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


def _path_token_indices(verb: str, payload: list[str]) -> list[int]:
    """定位"哪 (哪些) token 是文件路径"（P16g #1：优先 ``using`` 后，防 varlist 混入）。

    规则（并集，均为"应送 DataPathAuditor"的候选）：
    1. ``using`` 之后的 token（varlist 在 using 前也不会漏审真正的文件名）；
    2. 任意带引号的 token（引号串 = 文件名/选项文件值）；
    3. 无引号但"像路径"的裸 token（含 ``./\\:`` 或已知扩展名），兜底 ``C:\\x``/`foo.dta`；
    4. 直接文件动词 save/saveold/cd/type 与 copy：无 varlist 歧义，其第一个真实参数是文件。
    use/infile/insheet 已从命令层移除（varlist 语法歧义不再进入路径审计）。
    """
    low = [t.strip('"').lower() for t in payload]
    idxs: list[int] = []
    for i, tok in enumerate(payload):
        if tok.startswith('"'):
            idxs.append(i)
        if low[i] == "using" and i + 1 < len(payload):
            idxs.append(i + 1)
    for i, tok in enumerate(payload):
        if tok.startswith('"') or low[i] in _KEYWORDS:
            continue
        # path-like 裸 token。注意：不含 ':' —— merge 键 `1:1`、`cellrange(A1:B2)`
        # 里的冒号是语法不是路径（P16h 误伤修复）；真正的冒号路径（如 C:\x）由
        # 直接文件动词/copy 首实参捕获后，再经"无引号含冒号"拒绝规则兜底。
        if any(ch in tok for ch in "./\\") or low[i].endswith(
            (".dta", ".csv", ".xlsx", ".do", ".dat", ".txt", ".log", ".gph", ".ster", ".raw")
        ):
            idxs.append(i)
    if verb in ("save", "saveold", "cd", "type", "copy"):
        real = [i for i, k in enumerate(low) if k not in _KEYWORDS]
        if verb == "copy":
            idxs += real[:2]
        elif real:
            idxs.append(real[0])  # 直接文件动词第一个真实参数必为文件
    return sorted(set(idxs))


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
        if verb in ("import", "export") and rest and _first_token(rest[0]) not in _FILE_KINDS:
            continue  # 非文件的 import/export（如 import idcode）

        idxs = _path_token_indices(verb, rest)
        for idx in idxs:
            if idx >= len(rest):
                continue
            tok = rest[idx]
            quoted = tok.startswith('"')
            path = tok[1:-1] if quoted else tok
            if not path:
                continue
            if not quoted and ":" in path:
                # 无引号却含冒号（如 C:secret / C:\x）：fail-closed，要求引号包完整路径
                return False, f"unquoted drive/URL path {path!r} is not allowed in restricted mode"
            ok, reason = _audit_one_path(path, auditor)
            if not ok:
                return False, reason
    return True, ""


def _has_file_write_subcommand(verb: str, toks: list[str], stmt: str) -> tuple[bool, str]:
    """verb 白名单不等于文件系统白名单：某些白名单命令的子命令会写外部文件。

    P16e #1：estimates/est save|use、label save、table export(...)/dofile 都写文件，
    这些路径不经 _FILE_COMMANDS 审计 → 直接整句拒绝（restricted 下不允许它们写盘）。
    返回 (is_file_write, reason)。
    """
    sub = _first_token(toks[1]) if len(toks) > 1 else ""
    low_stmt = stmt.lower()
    if verb in ("estimates", "est") and sub in ("save", "use"):
        return True, f"'{verb} {sub}' writes/reads estimation files; not allowed in restricted mode"
    if verb == "label" and sub == "save":
        return True, "label save writes a do-file; not allowed in restricted mode"
    if verb == "table" and ("export(" in low_stmt or "dofile" in low_stmt or sub == "export"):
        return True, "table export/dofile writes a file; not allowed in restricted mode"
    return False, ""


def check_commands(code: str) -> tuple[bool, str]:
    """命令白名单 fail-closed（P16c #2）：不在 _ALLOWED_VERBS 的命令一律拒绝。

    import/export 等按子命令判断（import excel 合法、python:/mata: 等非法）。
    这使得 unknown 命令**默认拒绝**（此前黑名单是未知放行——哲学差别）。
    P16e #1：白名单内也有子命令会写文件（estimates/label/table），逐句拦。
    """
    for stmt in _split_statements(code):
        toks = _tokens(stmt)
        if not toks:
            continue
        verb = _first_token(toks[0])
        # 白名单内子命令的文件写入
        if verb in ("estimates", "est", "label", "table"):
            is_write, reason = _has_file_write_subcommand(verb, toks, stmt)
            if is_write:
                return False, reason
        # 两词/子命令形态
        if verb in ("import", "export", "graph", "putexcel", "outfile", "filefilter"):
            if verb in ("import", "export") and len(toks) > 1:
                sub = _first_token(toks[1])
                if verb == "import" and sub in _FILE_KINDS:
                    continue  # import <kind> 走文件审计，白名单放行
                if verb == "export" and sub in _FILE_KINDS:
                    continue
            return False, f"command '{verb}' is not allowed in restricted mode"
        if verb not in _ALLOWED_VERBS:
            return False, f"command '{verb}' is not allowed in restricted mode"
    return True, ""


def restrict(code: str, auditor: DataPathAuditor) -> tuple[bool, str]:
    """受限模式总闸（fail-closed）：危险命令 → 命令白名单 → 越权路径。"""
    ok, reason = check_dangerous(code)
    if not ok:
        return False, reason
    ok, reason = check_commands(code)
    if not ok:
        return False, reason
    ok, reason = check_file_paths(code, auditor)
    if not ok:
        return False, reason
    return True, ""
