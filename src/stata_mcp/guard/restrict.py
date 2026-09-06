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

# 带文件路径的命令：命令名 ... "path"
_FILE_PATH_RE = re.compile(
    r'(?:(?:^|\s)(?:use|import\s+(?:delimited|excel|spss|sasxport|sav|dbase|haver|infix|infile)|save|cd|append|merge)\s+)"([^"]+)"',
    re.IGNORECASE,
)


def _strip_comment(line: str) -> str:
    """去掉行内注释（// 与 /* */ 简化处理；* 行首注释由 _command_word 返回空处理）。"""
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


def _command_word(line: str) -> str:
    """提取命令首词（小写）；空行/纯注释行返回 ""。"""
    line = line.strip()
    if not line or line.startswith("*"):
        return ""
    line = _strip_comment(line).strip()
    if not line:
        return ""
    line = _strip_prefix(line)
    m = re.match(r"^(\S+)", line)
    return m.group(1).lower() if m else ""


def check_dangerous(code: str) -> tuple[bool, str]:
    """检测 shell 逃逸 / 文件删除。返回 (allowed, reason)。"""
    for raw_line in code.splitlines():
        line = _strip_comment(raw_line).strip()
        if not line or line.startswith("*"):
            continue
        # shell 逃逸：! 在行首（剥前缀后）
        stripped = _strip_prefix(line).strip()
        if stripped.startswith("!"):
            return False, "shell escape (!) is blocked in restricted mode"
        cmd = _command_word(raw_line)
        if cmd in _DANGEROUS_COMMANDS:
            return False, f"command '{cmd}' is blocked in restricted mode"
    return True, ""


def check_file_paths(code: str, auditor: DataPathAuditor) -> tuple[bool, str]:
    """检测带文件路径命令的路径是否越权。返回 (allowed, reason)。"""
    for raw_line in code.splitlines():
        line = _strip_comment(raw_line)
        if not line or line.startswith("*"):
            continue
        for m in _FILE_PATH_RE.finditer(line):
            path = m.group(1)
            # 相对路径/URL 判断交给 auditor；本地路径越权即拒
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


def restrict(code: str, auditor: DataPathAuditor) -> tuple[bool, str]:
    """受限模式总闸：先查危险命令，再查越权路径。返回 (allowed, reason)。"""
    ok, reason = check_dangerous(code)
    if not ok:
        return False, reason
    ok, reason = check_file_paths(code, auditor)
    if not ok:
        return False, reason
    return True, ""
