"""stata_get_help 工具：查 Stata 官方命令帮助（P1a）。

为什么用 findfile + 读 .sthlp 而不是 help 命令：pystata 里 `help <cmd>` 会打开
GUI 帮助窗口（卡住/占桌面），不可靠。findfile 定位到 .sthlp（SMCL 文件），
读文件 + 剥 SMCL 标签返回纯文本。

topic 校验（防注入）：用 is_valid_identifier 白名单，防止把路径/引号拼进 findfile。
"""
from __future__ import annotations

import os

from ..envelope import Envelope
from ..guard.validate import is_valid_identifier
from ..output.smcl import strip_smcl
from . import register
from .run import _resolve_backend, _session_arg

_STATA_GET_HELP_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "topic": {
            "type": "string",
            "description": "要查询的 Stata 命令/主题（如 regress、xtreg、ereturn）。",
        }
    },
    "required": ["topic"],
}


def _read_help_file(path: str) -> str:
    """读 .sthlp 文件 → 剥 SMCL 标签。编码尝试 UTF-8 → GBK。"""
    with open(path, "rb") as fh:
        data = fh.read()
    for enc in ("utf-8", "gbk"):
        try:
            return strip_smcl(data.decode(enc))
        except (UnicodeDecodeError, ValueError):
            continue
    return strip_smcl(data.decode("utf-8", "replace"))


@register("stata_get_help", _STATA_GET_HELP_SCHEMA)
def stata_get_help(arguments: dict, ctx=None) -> Envelope:
    """查 Stata 官方帮助（纯文本，从 .sthlp 读）——agent 不必瞎猜命令语法。"""
    args = arguments if isinstance(arguments, dict) else {}
    topic = args.get("topic", "")
    meta = {"tool": "stata_get_help"}

    if not isinstance(topic, str) or not topic.strip():
        return Envelope(
            text="error: 'topic' is required",
            structured=None, rc=1, error_class=None, graphs=[], meta=meta,
        )
    topic = topic.strip().split()[0] if topic.strip() else ""
    if not is_valid_identifier(topic):
        return Envelope(
            text=f"error: invalid help topic {topic!r}",
            structured=None, rc=1, error_class=None, graphs=[], meta=meta,
        )

    session = _resolve_backend(ctx, _session_arg(args))
    # findfile 输出文本含 .sthlp 路径（末行非空），也可从 r(fn) 读；这里取文本末行。
    r = session.execute(f"findfile {topic}.sthlp")
    if r.rc != 0 or not r.text.strip():
        return Envelope(
            text=f"no help found for '{topic}' (not an installed command?)",
            structured=None, rc=r.rc or 1, error_class=None, graphs=[],
            meta=meta,
        )

    lines = [ln.strip() for ln in r.text.splitlines() if ln.strip()]
    path = lines[-1] if lines else ""
    if not path.lower().endswith(".sthlp") or not os.path.exists(path):
        return Envelope(
            text=f"could not locate help file for '{topic}'",
            structured=None, rc=1, error_class=None, graphs=[], meta=meta,
        )

    try:
        help_text = _read_help_file(path)
    except OSError:
        return Envelope(
            text=f"could not read help file for '{topic}'",
            structured=None, rc=1, error_class=None, graphs=[], meta=meta,
        )

    # 帮助可能很长，截断避免撑爆上下文。
    if len(help_text) > 8000:
        help_text = help_text[:8000] + "\n...[help truncated; use a fuller client-side docs]..."
    return Envelope(
        text=help_text,
        structured={"topic": topic, "source": path, "chars": len(help_text)},
        rc=0,
        error_class=None,
        graphs=[],
        meta=meta,
    )
