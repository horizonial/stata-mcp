"""SMCL 标签清理 + 命令回显去噪（纯函数，可单测）。

P2 目标是"够用"：把 Stata 结果里形如 ``{txt}`` ``{res}`` ``{cmd}`` ``{hline 20}``
的 SMCL 控制标签剥掉，并去掉交互回显行（``. command``），让 LLM 看到干净文本。
不做像素级还原；DESIGN.md 已把"回显去噪要更可靠"列为待续项。
"""
from __future__ import annotations

import re

_SMCL_TAG = re.compile(r"\{[^{}]*\}")


def _drop_smcl_tags(text: str) -> str:
    out = text
    # SMCL 标签偶有嵌套（如 {sf SERIF:{hi ...}}），逐层剥最内层
    for _ in range(8):
        new = _SMCL_TAG.sub("", out)
        if new == out:
            break
        out = new
    return out


def _is_echo_line(line: str) -> bool:
    """回显/续行噪声行：以 ``. `` 或 ``> `` 开头（Stata 交互提示符形态）。"""
    s = line.lstrip()
    if s.startswith((".", ">")):
        return len(s) == 1 or s[1].isspace()
    return False


def strip_smcl(text: str) -> str:
    """剥掉 SMCL 标签与回显行，返回清洗后的多行文本。"""
    cleaned = _drop_smcl_tags(text)
    lines = [ln.rstrip() for ln in cleaned.splitlines() if not _is_echo_line(ln)]
    return "\n".join(lines).strip("\n")
