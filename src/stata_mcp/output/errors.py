"""Stata rc → Envelope.error_class 分类（ARCHITECTURE.md §6.2 字段）。

为什么只做"已知少量 rc 精确映射"而不对未知 rc 猜类别：
- rc 是 Stata 官方错误码，语义精确稳定；对未知 rc 硬猜类别反而引入误分类，
  交给上层 agent 读 text 自行判断更可靠（未知 → None）。
- 因此这里就一张小映射表 + 一次查表，保持简单（任务硬约束）。

为什么函数签名仍带 ``text``：
- 当前判定只看 rc；但错误分类未来可能要覆盖"rc 不典型、错误文本特征明显"
  的案例（P4 guard / 更多类别），先留位避免改调用方。
"""
from __future__ import annotations

# rc → 类别（映射表越小越好，只放语义明确的官方错误码）：
#   111  = 变量 / 数据集不存在（variable/file not found）
#   198  = 语法错误 / 语句上下文不对（invalid syntax）
#   100  = 无效命令（not allowed / unknown command）
#   430  = 数值求解不收敛（convergence not achieved）
#   2000 = 样本为空（no observations）
_RC_TO_CLASS: dict[int, str] = {
    111: "not_found",
    198: "syntax",
    100: "syntax",
    430: "convergence",
    2000: "sample",
}


def classify_error(rc: int, text: str) -> str | None:
    """把 rc/文本映射到 error_class。

    返回 ``"not_found" | "syntax" | "convergence" | "sample"`` 之一；
    rc == 0 或 rc 不在映射表（未知错误）返回 None，不强凑类别。
    """
    if rc == 0:
        return None
    return _RC_TO_CLASS.get(rc)
