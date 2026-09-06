"""编码探测链：UTF-8 -> GBK -> latin-1（DESIGN D4）。

D4 依据（spike02）：Stata 18 text log 实测为 UTF-8；
GBK 只作中文老文件的回退；latin-1 永不失败，作最后兜底。
P2 的 D3 进程内 stdout 捕获本身是 str，用不到 bytes；
此模块为批处理/读日志等路径预留，保持纯函数可单测。
"""
from __future__ import annotations


def decode_bytes(data: bytes) -> str:
    """按 UTF-8 → GBK → latin-1 顺序解码 bytes，返回 str。"""
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    # latin-1 能映射任意字节，实际上到不了这里；仅为类型完整而保留
    return data.decode("utf-8", errors="replace")  # pragma: no cover
