"""输出截断：防止 agent 一次拿到超大输出撑爆上下文 / MCP 帧（P0-1）。

为什么要截断：``stata_run`` 返回完整 text，agent 若跑 ``list``/``browse`` 大数据，
输出可能几十 MB。head+tail 保留两头信息（开头是主要结果、结尾是错误/汇总），
中间省略，并显式标记截断，agent 可按需取完整内容。
"""
from __future__ import annotations

# 单次输出上限（字符）。超过则 head+tail 保留。
DEFAULT_MAX_CHARS = 20000
# head / tail 分配：head 占 70%，tail 占 30%（错误/汇总常在结尾）
_HEAD_RATIO = 0.7


def truncate(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> tuple[str, bool]:
    """按 max_chars 截断 text。返回 (text, truncated)。

    长度 ≤ max_chars 原样返回；超限保留 head（前 70%）+ tail（后 30%），
    中间插入省略标记。head/tail 都按"行"截断，避免切断一行。
    """
    if len(text) <= max_chars:
        return text, False

    head_chars = int(max_chars * _HEAD_RATIO)
    tail_chars = max_chars - head_chars

    # 取前 head_chars 字符所在的行边界
    head = text[:head_chars]
    if "\n" in head:
        head = head[: head.rfind("\n")]
    # 取后 tail_chars 字符所在的行边界
    tail = text[-tail_chars:]
    if "\n" in tail:
        tail = tail[tail.find("\n") + 1:]

    marker = f"\n...[output truncated at {max_chars} chars; original {len(text)} chars]...\n"
    return head + marker + tail, True
