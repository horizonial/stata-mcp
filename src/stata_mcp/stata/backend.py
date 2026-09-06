"""ExecutionBackend 协议与最小执行结果类型（ARCHITECTURE.md §6.1）。

P2 只实现 PystataBackend；抽象先立好，未来可加批处理 Stata / R / SAS，
改一行装配即可，不碰 core。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class ExecutionResult:
    """一次后端执行的原始结果：捕获到的文本 + Stata 返回码。

    ``e_changed``：本次执行是否改变了内存里的 e() 结果（= 是否跑了估计命令）。
    由 backend 用执行前后的 e() 指纹比较得出；这是结构化结果的路由信号，
    用来避免"跑完 regress 再跑 display，却附上上一次回归的过期结果"。
    """

    text: str
    rc: int
    e_changed: bool = False


class ExecutionBackend(Protocol):
    """Stata 执行后端抽象。"""

    def init(self) -> None: ...

    def execute(self, code: str, *, timeout: float | None = None) -> ExecutionResult: ...

    def interrupt(self) -> None: ...  # 预期：sfi.breakIn 走独立线程（P5）

    def close(self) -> None: ...

    def capabilities(self) -> dict: ...  # {"struct_results": bool, "graphs": bool, ...}
