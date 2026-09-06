"""结构化结果解析器（ARCHITECTURE.md §6.5，P3）。

import 本包即完成全部 parser 注册：``parser.py`` 定义注册表与路由主函数，
``regression.py`` 等模块通过 ``@register(...)`` 填充 PARSERS。
调用方只需 ``from stata_mcp.results import try_parse``。

加一种新结果类型 = 新放一个 parser 模块 + 在本文件加一行 import（模块自己的
``@register`` 会完成命令注册），不改 core / server / run.py。
"""
from __future__ import annotations

from . import parser as parser
from . import regression as regression  # noqa: F401  # import 即触发 @register 填充 PARSERS
from .parser import PARSERS, ResultParser, register, try_parse  # noqa: F401
