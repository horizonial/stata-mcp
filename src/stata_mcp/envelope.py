"""工具统一返回信封（ARCHITECTURE.md §6.2）。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Envelope:
    """所有 MCP 工具 handler 的统一返回类型。

    文本与结构化结果分离：LLM/agent 阅读 ``text``；
    ``structured`` 留给 P3 的 ResultParser 填充；rc/error_class 供上层决策；
    ``error`` 是统一的结构化错误对象（报错托底，P13）。
    """

    text: str
    structured: dict | None
    rc: int
    error_class: str | None  # "syntax" | "sample" | "convergence" | "not_found" | None
    graphs: list[str] = field(default_factory=list)  # 图路径（P5 起）
    meta: dict = field(default_factory=dict)  # 耗时、截断标记等
    error: dict | None = None  # 结构化错误 {kind, rc, class, message, command}
    images: list[tuple[str, bytes]] = field(default_factory=list)  # (mime, bytes)，P0-2
