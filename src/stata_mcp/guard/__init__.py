"""guard 包：安全审计基础设施（ARCHITECTURE.md §4，P4 交付 L1 参数校验 + L3 数据路径审计）。

本包只依赖标准库，import 不触发 pystata / mcp，也不碰 Stata 引擎。
L2 命令静态展开（黑名单）与 L4 资源监控不在本阶段范围（见 P5+/受限模式）。
"""
from __future__ import annotations

from .validate import (
    is_safe_filename,
    is_valid_identifier,
    is_valid_varname,
    validate_varname,
)
from .data_path import DataPathAuditor

__all__ = [
    # L1：参数校验
    "is_valid_varname",
    "is_valid_identifier",
    "validate_varname",
    "is_safe_filename",
    # L3：数据路径审计
    "DataPathAuditor",
]
