"""隐私哈希日志：路径 / URL 脱敏后落日志（ARCHITECTURE.md §4 配置层，P4）。

吸收 mcp-for-stata 的"路径哈希后打日志"思路，自研实现：
- 目的是"日志里可辨认是哪个文件、但不泄露完整路径（用户名 / 目录结构 / URL 凭据）"；
- 一律纯函数、确定性哈希（同一输入 → 同一脱敏串），方便日志跨行对账。

脱敏原则：
- 本地路径：保留盘符 + 文件名（方便排障定位到文件），中间目录整体哈希成 12 位 hex；
- URL：保留 scheme + 端口 + 末尾文件名；host 与中间路径哈希，query/fragment/userinfo
  整个丢弃（token/凭据最常藏在 query 里）。

为什么哈希整段中间目录 / host 而不是"只挡用户名"：
- 目录名本身可能就是敏感信息（项目代号、客户名），逐个挡漏得多；
- 哈希粒度是"中间全部 → 一个 token"，不泄露任何一级目录结构。
"""
from __future__ import annotations

import hashlib
import os
from urllib.parse import urlsplit

# 无法解析时的统一占位：宁可不可读，也不泄露原始输入
_REDACTED = "<redacted>"

# 哈希前缀长度：12 hex = 48 bit，碰撞概率对"日志对账"场景足够低
_HASH_LEN = 12


def _hash(text: str) -> str:
    """确定性 12 位 hex 哈希。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:_HASH_LEN]


def redact_path(p: str) -> str:
    """把路径脱敏成可辨认形式。

    例：``C:\\Users\\alice\\data\\foo.dta`` → ``C:\\<3f2a1b...>\\foo.dta``
    - 保留盘符与最后一段文件名；中间的目录（无论几级）整体哈希成一个 token；
    - 以分隔符结尾的"目录形态"输入：最后一段也是目录名，一并哈希，不保留；
    - 相对路径/无盘符：同样只保留文件名，目录哈希；
    - 非字符串 / 解析失败 → ``<redacted>``（fail-closed，绝不回退原始串）。
    """
    if not isinstance(p, str) or not p:
        return _REDACTED
    # 目录形态 = 原始输入以分隔符结尾（normpath 会剥掉尾分隔符，先记住再归一）
    dir_like = p.rstrip().endswith(("\\", "/"))
    try:
        norm = os.path.normpath(p)
        drive, tail = os.path.splitdrive(norm)
        segs = [s for s in tail.split(os.sep) if s]
    except (OSError, ValueError):
        return _REDACTED
    if not segs:
        # 纯盘符/根目录：本身没有目录信息可泄露
        return drive or _REDACTED

    if dir_like:
        head, leaf = segs, None
    else:
        head, leaf = segs[:-1], segs[-1]

    parts: list[str] = []
    if drive:
        parts.append(drive.rstrip("\\/"))
    if head:
        parts.append(f"<{_hash(os.sep.join(head))}>")
    if leaf:
        parts.append(leaf)
    return "\\".join(parts)


def redact_url(u: str) -> str:
    """把 URL 脱敏：只保留 scheme + 端口 + 末尾文件名，host 与中间路径哈希。

    例：``https://alice:hunter2@stats.example.com/data/file.csv?token=x``
        → ``https://<1a2b3c...>/file.csv``
    - userinfo（user:pass@）与 query/fragment 整个丢弃（凭据 / token 最常在这两处）；
    - 非 http(s) / 解析失败 / 无 host → ``<redacted>``。
    """
    if not isinstance(u, str) or not u.strip():
        return _REDACTED
    try:
        parts = urlsplit(u)
        scheme = parts.scheme.lower()
        if scheme not in ("http", "https"):
            return _REDACTED
        host = parts.hostname  # 已小写、去 userinfo/端口/方括号
        if not host:
            return _REDACTED
        try:
            port = parts.port  # 非法端口 → ValueError，走外层回退
        except ValueError:
            return _REDACTED

        authority = f"<{_hash(host)}>" if port is None else f"<{_hash(host)}>:{port}"
        segs = [s for s in parts.path.split("/") if s]
        if not segs:
            return f"{scheme}://{authority}/"

        leaf = segs[-1]
        # 最后一段像文件名（含 . ）才保留，便于排障；纯目录路径整段哈希
        if "." in leaf:
            head = segs[:-1]
            path_tok = f"<{_hash('/'.join(head))}>/{leaf}" if head else leaf
        else:
            path_tok = f"<{_hash('/'.join(segs))}>"
        return f"{scheme}://{authority}/{path_tok}"
    except Exception:
        # 任何解析异常都回退占位，绝不泄露原始 URL
        return _REDACTED


__all__ = ["redact_path", "redact_url"]
